import cv2
import datetime
import http.server
import io
import json
import math
import numpy
import signal
from socketserver import ThreadingMixIn
import subprocess
import time
import threading
import urllib
from picamera import PiCamera, Color

class Info(threading.Thread):
    def __init__(self, mjpeg_streams):
        threading.Thread.__init__(self)
        self.streams = mjpeg_streams
        self.running = True

        self.start()

    def run(self):
        while self.running:
            print("MJPEG Streamers: %d" % len(self.streams))
            time.sleep(1)

    def done(self):
        self.running = False


class Timer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.startTime = {}
        self.cumulativeTime = {}
        self.counts = {}
        self.running = True

        self.start()

    def begin(self, name):
        self.startTime[name] = time.perf_counter()

    def end(self, name):
        if name not in self.cumulativeTime:
            self.cumulativeTime[name] = 0.00
            self.counts[name] = 0
        self.cumulativeTime[name] += time.perf_counter() - self.startTime[name]
        self.counts[name] += 1

    def run(self):
        while self.running:
            # Report every second
            time.sleep(1)
            continue
            for key in self.counts:
                if self.counts[key] > 0:
                    avg = self.cumulativeTime[key] / self.counts[key]
                    print("Avg %s time: %f" % (key, avg))
    def done(self):
        self.running = False

class ToVideo(threading.Thread):
    def __init__(self, settings):
        threading.Thread.__init__(self)

        self.running = True
        self.event = threading.Event()
        self.fps = settings['fps']
        self.width = settings['width']
        self.height = settings['height']

        self.frames = []

        self.start()
    
    def run(self):
        videoWriter = None
        videoWriterOutput = ''

        while self.running == True:
            # Wait until we get the signal that there is work to do
            # This may be frames present, or cleanup work
            self.event.wait()
            # clear the event right away since we're going to fetch all frames needing processing
            self.event.clear()

            while len(self.frames) > 0 and self.running == True:
                frame = self.frames.pop(0)

                if isinstance(frame, str):
                    # start of video
                    filename = 'videos/' + frame + '.mp4'
                    # These ffmpeg args work well for 720p@20
                    args = ['ffmpeg', '-s', '%dx%d' % (self.width, self.height), '-f', 'image2pipe', '-framerate', str(self.fps), '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '23', '-preset', 'ultrafast', '-movflags', '+faststart', '-threads', '3', filename]
                    print("Saving to %s" % filename)
                    videoWriter = subprocess.Popen(args, bufsize=0, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                elif frame == False:
                    # end of video
                    # Close Video Writer
                    #videoWriter.wait()
                    videoWriterOutput, errs = videoWriter.communicate()
                    # TODO: printing these might not be necessary
                    #print(videoWriterOutput)
                    #print(errs)

                    print('Finished writing video')
                    videoWriter.stdin.close()
                    videoWriter.stdout.close()
                    videoWriter.stderr.close()
                    # TODO: we should really wait until ffmpeg is done before continuing, but need to handle terminating it too
                    #videoWriter.wait()
                    videoWriterOutput = ''
                    videoWriter = None
                else:
                    # standard frame, keep pushing to ffmpeg
                    #outs, errs = videoWriter.communicate(input=currentFrame)
                    #outs, errs = videoWriter.communicate()
                    #print(outs, errs)
                    toBeWritten = len(frame)
                    written = videoWriter.stdin.write(frame)
                    if written != toBeWritten:
                        print('ERROR: Tried to send %d bytes to ffmpeg, but only sent %d.' % (toBeWritten, written))

        print('ToVideo exiting')
        # we are no longer running, so close the handle to ffmpeg
        if videoWriter:
            videoWriterOutput, errs = videoWriter.communicate()
            print(videoWriterOutput)
            print(errs)
            videoWriter.stdin.close()
            videoWriter.stdout.close()
            videoWriter.stderr.close()

    def done(self):
        self.running = False


class MotionDetection(threading.Thread):
    def __init__(self, settings, frameHandler):
        threading.Thread.__init__(self)

        self.settings = settings
        self.configure()
        self.running = True
        self.event = threading.Event()
        self.frameHandler = frameHandler

        self.toVideo = ToVideo(settings)
        self.checkAfterTimestampDelta = 0.2
        self.stopRecordingAfterTimestampDelta = 10

        self.motionDetected = False
        # Keep last time we saw motion
        self.motionAtTimestamp = 0

        self.start()

    def configure(self):
        settings = self.settings
        self.fps = settings['fps']
        self.width = settings['width']
        self.height = settings['height']
        # TODO: I'm fuzzy on this, fix it
        self.sensitivityPercentage = settings['sensitivityPercentage'] / 100

        # 0.8% of white pixels signals motion
        cutoff = math.floor(self.width * self.height * self.sensitivityPercentage)
        # Pixels with motion will have a value of 255
        # Sum of 1% of pixels having value of 255 is ...
        self.cutoff = cutoff * 255
    
    def run(self):
        width = self.width
        height = self.height
        fps = self.fps

        previousFrame = None
        currentFrame = None
        numpyFrame = None

        motionAtTimestamp = 0
        checkAfterTimestamp = 0
        updateDetectStillAfterTimestamp = 0
        # TODO: time.time() + 5 # wait 5 seconds before beginning motion detection
        stopRecordingAfterTimestamp = 0

        while self.running == True:
            # Wait until we get the signal that there is work to do
            # This may be frames present, or cleanup work
            self.event.wait()
            # clear the event right away since we're going to fetch all frames needing processing
            self.event.clear()

            (currentFrame, currentFrameTimestamp) = self.frameHandler.getFrame()
            while currentFrame:
                # Only check for motion every 200m
                if currentFrameTimestamp > checkAfterTimestamp:
                    checkAfterTimestamp = currentFrameTimestamp + self.checkAfterTimestampDelta

                    # Copy into numpy/opencv so we can do motion detection
                    img_np = numpy.frombuffer(currentFrame, dtype=numpy.uint8)
                    
                    numpyFrame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)

                    #profile.begin('grayscale-diff-threshold')
                    grayscale = cv2.cvtColor(numpyFrame, cv2.COLOR_RGB2GRAY)
                    # on our first run remember the first frame and skip motion detection
                    if previousFrame is None:
                        previousFrame = grayscale
                        (currentFrame, currentFrameTimestamp) = self.frameHandler.getFrame()
                        continue

                    which = grayscale
                    # Clear out region we want to ignore motion in
                    for region in self.settings['ignore']:
                        x = region[0]
                        y = region[1]
                        while y < region[3]:
                            which[y, x:region[2]] = 0
                            y += 1
                    #profile.end('grayscale-diff-threshold')

                    frameDiff = cv2.absdiff(previousFrame, grayscale)
                    frameThreshold = cv2.threshold(frameDiff, 25, 255, cv2.THRESH_BINARY)[1]

                    # MOVE THIS UP A LEVEL
                    # Store frame of changed pixels so we can show it in the UI
                    which = frameThreshold
                    if currentFrameTimestamp > updateDetectStillAfterTimestamp:
                        # convert back to mjpeg
                        ret, buf = cv2.imencode('.jpg', which)
                        jpeg = numpy.array(buf).tostring()
                        streamer.httpd.motion = jpeg
                        updateDetectStillAfterTimestamp = currentFrameTimestamp + 0.5

                    # Add up all pixels. Pixels with motion will have a value of 255
                    pixelSum = numpy.sum(frameThreshold)
                    if pixelSum > self.cutoff: # motion detected in frame
                        # Log that we are seeing motion
                        self.motionDetected = True

                        # Tell writer the timestamp if we've detected new motion
                        if motionAtTimestamp == 0:
                            # convert milliseconds into timestamp for
                            ts = datetime.datetime.fromtimestamp(currentFrameTimestamp).strftime('%Y%m%d%H%M%S') + ('_%dx%d_%d' % (width, height, fps))
                            self.toVideo.frames.append(ts)
                            self.toVideo.event.set()
                            
                        self.motionAtTimestamp = motionAtTimestamp = currentFrameTimestamp
                        # Stop recording after 10 seconds of no motion
                        stopRecordingAfterTimestamp = currentFrameTimestamp + self.stopRecordingAfterTimestampDelta                        

                    # Use current frame in next comparison
                    previousFrame = grayscale

                # End conditional frame comparison logic

                if motionAtTimestamp > 0:
                    if stopRecordingAfterTimestamp < currentFrameTimestamp:
                        # Tell writer we haven't seen motion for a while
                        print("%d seconds without motion" % self.stopRecordingAfterTimestampDelta)

                        self.toVideo.frames.append(False)
                        self.toVideo.event.set()
                        motionAtTimestamp = 0
                        # Log that we are no longer seeing motion
                        self.motionDetected = False
                    else:
                        self.toVideo.frames.append(currentFrame)
                        self.toVideo.event.set()
                (currentFrame, currentFrameTimestamp) = self.frameHandler.getFrame()

            # no more frames to work with
            #print('frame list is empty')
        print('MotionDetection exiting')

    def done(self):
        self.running = False
        self.toVideo.done()
        self.toVideo.event.set()


mjpeg_outputs = []
class requestHandler(http.server.BaseHTTPRequestHandler):
    #def __init__(self):
    #    http.server.BaseHTTPRequestHandler.__init__(self)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == '/':
            html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta charset="utf-8" />
<style>
html, body, div,img {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}
div {
    width: 100%;
}
#status {
    height: 10px;
}
#nav {
    background-color: #7cafc2;
    display: grid;
    grid-template-columns: auto auto auto;
}
#nav > div {
    border-bottom: 3px solid #7cafc2;
    color: #fff;
    height: 3em;
    line-height: 3em;
    text-align: center;
}
#nav > div.success {
    border-color: #f8f8f8;
}
</style>
</head>
<body>
<div id="status">
</div>
<div id="nav">
    <div data-handleClass="nav" data-action="play">Stream</div>
    <div data-handleClass="nav" data-action="slow">Slow</div>
    <div data-handleClass="nav" data-action="detect">Pixels</div>
</div>
<div>
    <img src="/stream.mjpeg" width="100%" id="img" />
</div>
<script>
var ajaxGet = function(url, callback) {
    var request = new XMLHttpRequest();
    request.open('GET', url, true);

    request.addEventListener('load', function(event) {
        var request = event.target;
        if (request.status >= 200 && request.status < 400) {
            // TODO: this is ugly
            var error;
            try {
                var data = JSON.parse(request.responseText);
            } catch (e) {
                error = "JSON parse: " + e.message;
            }
            if (error) {
                callback(error);
            } else {
                callback(null, data);
            }
        } else {
            // We reached our target server, but it returned an error
            callback('Did not get 20x or 30x HTTP status');
        }
    });

    request.addEventListener('error', function(event) {
        callback('GET failed. Did we lose connectivity?');
    });

    request.send();
};

var tag = function(tagName, attributes, children) {
    var element = document.createElement(tagName);
    for (var i in attributes) {
        element.setAttribute(i, attributes[i]);
    }
    // Convert text to text node
    for (var i = 0; i < children.length; i ++) {
        var node = children[i];
        if (node == null) {
            continue;
        } else if (node instanceof Node) {
        } else {
            node = document.createTextNode(node);
        }
        element.appendChild(node);
    }

    return element;
};
var drawChildren = function(container, children) {
    /*
    More room for cool optimizations here:
    - loop through current and desired children, compare using node types, merge differences if possible
    */
    // perhaps compare element ids

    while (container.firstChild) {
        container.removeChild(container.firstChild);
    }
    children.forEach(function(item) {
        if (item == null) {
            return;
        }
        container.appendChild(item);
    });
};

var handles = {
    img: document.getElementById('img')
};

var nav = {
    interval: null,
    handles: {
        container: document.getElementById('nav')
    },
    init: function() {
        var self = this;
    },
    _clearInterval: function() {
        var self = this;
        if (self.interval) {
            clearInterval(self.interval);
        }
    },
    play: function(target, e) {
        var self = this;
        self._selectTab(target, 'nav');
        self._clearInterval();
        handles.img.src = '/stream.mjpeg';
    },

    slow: function(target, e) {
        var self = this;
        self._selectTab(target, 'nav');
        
        self._clearInterval();
        self.interval = setInterval(
            function() {
                handles.img.src = '/still.jpeg?t=' + Date.now();
            },
            1000
        );
    },
    detect: function(target, e) {
        var self = this;
        self._selectTab(target, 'nav');

        self._clearInterval();
        self.interval = setInterval(
            function() {
                handles.img.src = '/motion.jpeg?t=' + Date.now();
            },
            1000
        );
    },
    _selectTab: function(el, handleClass) {
        var self = this;
        self.handles[handleClass].forEach(function(sibling) {
            sibling.classList.remove('success');
        });
        el.classList.add('success');
    }

};

var modules = [
    nav
];
var getHandles = function(obj) {
    var container = obj.handles.container;
    container.querySelectorAll('[data-handle]').forEach(function(el) {
        obj.handles[ el.getAttribute('data-handle') ] = el;
    });

    container.querySelectorAll('[data-handleClass]').forEach(function(el) {
        var handleClass = el.getAttribute('data-handleClass');
        if (!(handleClass in obj.handles)) {
            obj.handles[ handleClass ] = [];
        }
        obj.handles[ handleClass ].push(el);
    });
};
modules.forEach(function(self) {
    getHandles(self);
    // automate this using data-action
    self.handles.container.addEventListener('click', function(event) {
        var target = event.target;
        var action;
        // go up a level if material icon was clicked
        if (target.tagName == 'I') {
            target = target.parentNode;
        }
        action = target.getAttribute('data-action');
        if (action in self) {
            self[action](target, event);
        }
    });
    self.init();
});




var statusHandle = document.getElementById('status');
setInterval(
    function() {
        ajaxGet('/status.json', function(error, data) {
            if (error) {
                console.log(error);
                return;
            }
            var ts = Date.now() / 1000;
            console.log(ts);
            var cutoff = 3600.00;
            var cutoff = 60 * 10; // yellow for 10 minutes
            if (data.motion) {
                document.body.style.backgroundColor = '#ab4642';
            } else {
                if (ts - data.motionAtTimestamp < cutoff) {
                    document.body.style.backgroundColor = '#f7ca88';
                } else {
                    document.body.style.backgroundColor = 'white';
                }
            }
        })
    },
    2000
);
</script>

</body>
</html>
            """
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(html.encode())
        elif path == '/status.json':
            data = {
                'motion': motionDetection.motionDetected,
                'motionAtTimestamp': motionDetection.motionAtTimestamp
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write( json.dumps(data).encode() )

        elif path == '/stream.mjpeg':
            self.event = threading.Event()
            self.running = True

            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=PICAM_MJPEG')
            self.end_headers()

            mjpeg_outputs.append(self)

            while self.running:
                self.event.wait()
                self.event.clear()
                if self.server.still:
                    # this doesn't seem to work
                    if self.wfile.closed or not self.wfile.writable():
                        break
                    try:
                        self.wfile.write(b'--PICAM_MJPEG\nContent-Type: image/jpeg\n\n')
                        self.wfile.write(self.server.still)
                    except BrokenPipeError as e:
                        break
            mjpeg_outputs.remove(self)

        elif path == '/still.jpeg':
            if self.wfile.closed or not self.wfile.writable():
                return
            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.end_headers()

            if self.server.still:
                # this doesn't seem to work
                try:
                    self.wfile.write(self.server.still)
                except BrokenPipeError as e:
                    return


        elif path == '/motion.jpeg':
            if self.wfile.closed or not self.wfile.writable():
                return
            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.end_headers()

            if self.server.still:
                # this doesn't seem to work
                try:
                    self.wfile.write(self.server.motion)
                except BrokenPipeError as e:
                    return

        else:
            # TODO: return 404
            return False

class ThreadedHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    """Handle requests in a separate thread."""

class Streamer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.outputs = []
        self.httpd = ThreadedHTTPServer(('0.0.0.0', 8080), requestHandler)
        self.start()
    
    def run(self):
        self.httpd.serve_forever()
           
        
    def done(self):
        print('Streamer exiting')
        self.httpd.shutdown()
        for output in mjpeg_outputs:
            output.running = False
            output.event.set()


class FrameHandler(object):
    def __init__(self, camera, settings):
        self.camera = camera
        self.fps = settings['fps']
        self.width = settings['width']
        self.height = settings['height']
        self.motionDetection = None

        #self.stampBackground = Color(y=0, u=0, v=0)
        #self.stampBackground = Color(y=225, u=0, v=148)
        self.frames = []
        self.frameTimestamps = []
        self.output = bytes()

        self.camera.annotate_background = Color(y=0, u=0, v=0)

        #self.unixtime = float( int(time.time()) )
        self.mjpeg = True

    def getFrame(self):
        if len(self.frames) > 0:
            #print('%d frames in list' % len(self.frames))
            # Return first frame in queue
            return (self.frames.pop(0), self.frameTimestamps.pop(0))
        return (None,None)

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # The annotation might be a frame off, but that's good enough
            self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Start of new frame; close the old one (if any) and open a new output
            self.frames.append( self.output )
            self.frameTimestamps.append( time.time() )
            # Signal to motion detection thread that there is work to be done
            self.motionDetection.event.set()

            # Snag copy of frame for mjpeg life stream output
            # This will be out of sync with the frames list
            if self.mjpeg:
                streamer.httpd.still = self.output
                for output in mjpeg_outputs:
                    #output.frame = self.output
                    output.event.set()
                self.mjpeg = False 
            else:
                self.mjpeg = True

            self.output = bytes()
        else:
            # don't believe i've ever seen this message
            print('buf does not start with magic bytes')
        self.output += buf

    def flush(self):
        i = 1
        print('flushed')


running = True
def signal_handler(sig, frame):
    global running
    running = False
    print('Exiting ...')
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


#profile = Timer()

#fps = 10
#width = 1920
#height = 1080


settings = {
    'fps': 20,
    'width': 1280,
    'height': 720,

    # If this percentage of pixels change between frames, clasify it as motion
    'sensitivityPercentage': 0.2,
    'ignore': [
        # [x-start, y-start, x-end, y-end]
        # left half
        #[0, 0, 640, 720]
        # top half
        #[0, 0, 1280, 360]
        # number region
        #[0, 0, 1280, 50]
    ]
}

# using the h264 encoder with three threads we can use crf, the ultrafast preset, and handle 1080p@10 quite well. it keeps up and memory doesn't keep growing
# usnig the hardware encoder, it can't keep up at 800k or 2000k bitrate, and there's no option for multiple threads since it's hardware. doesn't keep up


# start threads for motion detection


#camera = PiCamera(resolution=(width,height), framerate=fps) #, sensor_mode=3)  #, sensor_mode=1)
with PiCamera(resolution=(settings['width'],settings['height']), framerate=settings['fps']) as camera: #, sensor_mode=3)  #, sensor_mode=1)
#with PiCamera(resolution='VGA') as camera: #, sensor_mode=3)  #, sensor_mode=1)
    camera.start_preview()
    time.sleep(2)

    streamer = Streamer()
    frameHandler = FrameHandler(camera, settings)
    motionDetection = MotionDetection(settings, frameHandler)
    info = Info(mjpeg_outputs)

    # grr circular handles :-)
    frameHandler.motionDetection = motionDetection
    # init threads here

    camera.start_recording(frameHandler, format='mjpeg', quality=100) #, use_video_port=False)
    i = 0
    while running == True:
        # Q: would it be useful to get a timestamp here of milliseconds?
        # thought is to use that timestamp to determine whether we're lagging on collection,
        # and ultimately to see if we got 30 frames in a second
        camera.wait_recording(1)
        continue

        i += 1
        if i > 10:
            print("%s @%d mode: %d shutter: %f exposure: %f" % (camera.resolution, camera.framerate, camera.sensor_mode, camera.shutter_speed, camera.exposure_speed))
            i = 0

    camera.stop_recording()
    camera.close()


# close out threads
info.done()
motionDetection.done()
motionDetection.event.set()
streamer.done()
    
