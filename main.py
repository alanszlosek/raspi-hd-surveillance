import cv2
import datetime
import glob
import http.server
import io
import json
import math
import numpy
import os
import pathlib
import random
import picamera
import signal
import subprocess
import threading
import time
import urllib

# to concat later, use concat protocol, which will allow you specify the framerate
# ffmpeg -framerate 10 -i "concat:20201025060604_before.h264|20201025060604_after.h264" -c:v copy out.mp4 

# raspi4 can handle 1088p 30fps and detect motion 2-3 times per second, while keeping CPU core around 80%
# when motion is detected, encoding to h264 and checking for motion every second lets the CPU go under 60%

# we convert frames to jpeg as quickly as we get them, which adds about 3% to CPU

# UPDATE: 20201104
# switched to pulling jpeg stills using capture_continuous
# and now we get faster frames for streaming, AND 74% CPU. an improvement!


# default settings
# if config.json is present, we'll use those
settings = {
    'fps': 30,
    'width': 1920,
    'height': 1088,
    #'fps': 20,
    #'width': 1280,
    #'height': 720,
    'sensitivityPercentage': 0.2,
    # pi4 can do 3 frames a second
    # pizero can do 2
    # but leaving this at 0.3 should take care of both
    'secondsBetweenDetection': 0.3,
    'ignore': [
        # for 720
        #[0, 0, 227, 396],
        #[227, 0, 1280, 340],
        #[0, 443, 1280, 720]
        # just ignore the top edge for testing
        #[0, 0, 1280, 100]
        # for 1080
	[0, 0, 1920, 662],
        [0, 775, 1920, 1088]
    ]
}
prior_image = None

detected_at = None
filename = 'bla'
cutoff = 0


class SplitFrames(object):
    def __init__(self):
        self.stream = io.BytesIO()
        # when we detect a full frame of JPEG data, we'll copy out those bytes
        self.buf = None
        self.count = 0
        # let's only copy buffer data out to support 10fps streaming
        self.cutoff = 0
        self.delta = 0.3

    def write(self, buf):
        if not buf.startswith(b'\xff\xd8'):
            print('ERROR: buffer with JPEG data does not start with magic bytes')

        # NOTE: until i see "buffer does not start with" happen, 
        # let's just use the buffer picamera gives us instead of copying into our self.stream
        self.buf = buf
        return

        if buf.startswith(b'\xff\xd8'):
            # Start of new frame; send the old one's length
            # then the data
            size = self.stream.tell()
            if size > 0:
                t = time.time()
                if t > self.cutoff:
                    #print('copying')
                    # seek to beginning of data
                    self.stream.seek(0)
                    self.buf = self.stream.read(size)
                    # then use it up to size
                    streamer.httpd.still = self.buf
                    self.cutoff = t + self.delta

                # now rewind for writing again
                self.stream.seek(0)
        else:
            # i've NEVER seen this happen
            print('buffer does not start with')
        self.stream.write(buf)

class MotionDetection:
    def __init__(self, camera, settings):
        self.camera = camera
        self.settings = settings

        self.previousFrame = None
        self.motionDetected = False
        self.motionAtTimestamp = 0
        self.checkAfterTimestamp = 0
        self.updateDetectStillAfterTimestamp = 0
        # TODO: time.time() + 5 # wait 5 seconds before beginning motion detection
        self.stopRecordingAfterTimestamp = 0
        self.stopRecordingAfterTimestampDelta = 2

        # for capturing to bgr
        #self.buffer = numpy.empty( (self.settings['width'] * self.settings['height'] * 3,), dtype=numpy.uint8)
        # set once ... since we're re-using the same buffer, we don't have to set it ever again
        #self.buffer.shape = (self.settings['height'], self.settings['width'], 3)

        # for capturing as jpeg
        self.buffer = SplitFrames()
        self.decoded = numpy.empty( (self.settings['height'], self.settings['width'], 3), dtype=numpy.uint8)
        self.grayscale = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.previous = None
        self.diff = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.threshold = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.ignore = numpy.ones( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.scratch = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)

        self.config(settings)

    def config(self, settings):
        # TODO: I'm fuzzy on this, fix it
        self.sensitivityPercentage = self.settings['sensitivityPercentage'] / 100

        # 0.8% of white pixels signals motion
        cutoff = math.floor(self.settings['width'] * self.settings['height'] * self.sensitivityPercentage)
        # Pixels with motion will have a value of 255
        # Sum of 1% of pixels having value of 255 is ...
        self.cutoff = cutoff * 255

        # assemble an ndarray of our ignore regions
        # we'll multiply this by our current frame to zero-out pixels we want to ignore
        for region in self.settings['ignore']:
            x = region[0]
            y = region[1]
            while y < region[3]:
                self.ignore[y, x:region[2]] = 0
                y += 1


    def detect(self, camera, continuous=False):
        global running
        global streamer
        self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if continuous:
            t = time.time()
            cutoff = t + self.settings['secondsBetweenDetection']
            for foo in self.camera.capture_continuous(self.buffer, format='jpeg', use_video_port=True, quality=100):
                if running == False:
                    break

                self.encodeStill()

                t = time.time()
                if t > cutoff:
                    # detection
                    print(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'))
                    self._detect(t)
                    if self.motionDetected:
                        break
                    cutoff = t + self.settings['secondsBetweenDetection']
                self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        else:
            print("SHOULD NOT GET HERE")
            self.camera.capture(self.buffer, format='bgr', use_video_port=True)
            self.encodeStill()
            self._detect(time.time())

        return self.motionDetected

    def encodeStill(self):
        global streamer
        streamer.httpd.still = self.buffer.buf

        return
        result, encoded = cv2.imencode('.jpg', self.buffer)
        if result:
            streamer.httpd.still = encoded
        else:
            print('failed to encode to jpeg')

    def _detect(self, currentFrameTimestamp):
        #profile.begin('grayscale-diff-threshold')
        img_np = numpy.frombuffer(self.buffer.buf, dtype=numpy.uint8)
        #numpyFrame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        self.decoded = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        #grayscale = cv2.cvtColor(numpyFrame, cv2.COLOR_BGR2GRAY)
        cv2.cvtColor(self.decoded, cv2.COLOR_BGR2GRAY, self.grayscale)
        # curious what dimensions of grayscale are ... and whether they match our width/height
        # and also wehther the size of this matches a normal WxHx3 byte array


        if self.previous is None:
            self.previous = self.grayscale.copy()
            return False

        cv2.absdiff(self.previous, self.grayscale, dst=self.diff)
        numpy.multiply(self.ignore, self.diff, out=self.scratch)
        # rely on numpy to ignore certain portions of the frame by multiplying those pixels by 0
        cv2.threshold(self.scratch, 25, 255, cv2.THRESH_BINARY, self.threshold)

        result, encoded = cv2.imencode('.jpg', self.diff)
        if result:
            streamer.httpd.diff = encoded

        # Add up all pixels. Pixels with motion will have a value of 255
        pixelSum = numpy.sum(self.threshold)
        if pixelSum > self.cutoff: # motion detected in frame
            # Log that we are seeing motion
            self.motionDetected = True
            self.motionAtTimestamp = currentFrameTimestamp
            # Stop recording after 10 seconds of no motion
            self.stopRecordingAfterTimestamp = currentFrameTimestamp + self.stopRecordingAfterTimestampDelta                        
            print('Seeing motion. Will stop recording after %s' % str(self.stopRecordingAfterTimestamp))

            # NOTE: let's only update previousFrame if there's motion
            # the thought is that we want to detect very slow moving objects ... objects that might not trigger 2% of pixel changes within 1/3 second
            # but that might over a longer time frame
            # Use current frame in next comparison
            self.previous = self.grayscale.copy()
        # End conditional frame comparison logic

        if self.motionDetected and self.stopRecordingAfterTimestamp < currentFrameTimestamp:
            # Tell writer we haven't seen motion for a while
            print("%d seconds without motion" % self.stopRecordingAfterTimestampDelta)

            # keep timestamp of last motion
            #self.motionAtTimestamp = 0
            # Log that we are no longer seeing motion
            self.motionDetected = False

        return self.motionDetected


mjpeg_outputs = []
class requestHandler(http.server.BaseHTTPRequestHandler):
    #def __init__(self):
    #    http.server.BaseHTTPRequestHandler.__init__(self)

    def log_message(self, *args):
        return

    def do_POST(self):
        global settings
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == '/config.json':
            contentLength = int(self.headers['Content-Length'])
            data = self.rfile.read(contentLength).decode('utf-8')
            o = json.loads(data)
            print('Updating settings', data)
            mergeConfig(o)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b"{}")


    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == '/':
            with open('index.html', 'r') as f:
                html = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            #self.send_headers('Content-Length', len(html))
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
            if self.server.still is None:
                return False

            still = self.server.still

            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', len(still))
            self.end_headers()
            # this doesn't seem to work
            try:
                self.wfile.write(still)
            except BrokenPipeError as e:
                # we don't care
                a = True
            except ConnectionResetError as e:
                # we don't care
                a = True
            return


        elif path == '/grayscale.jpeg':
            if self.wfile.closed or not self.wfile.writable():
                return
            if self.server.grayscale is None:
                return False

            still = self.server.grayscale

            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', len(still))
            self.end_headers()
            # this doesn't seem to work
            try:
                self.wfile.write(still)
            except BrokenPipeError as e:
                # we don't care
                a = True
            except ConnectionResetError as e:
                # we don't care
                a = True
            return


        elif path == '/motion.jpeg':
            if self.wfile.closed or not self.wfile.writable():
                return
            if self.server.motion is None:
                return False
            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.end_headers()

            if self.server.still:
                # this doesn't seem to work
                try:
                    self.wfile.write(self.server.motion)
                except BrokenPipeError as e:
                    print('brokenPipe2')
                    return

        else:
            # TODO: return 404
            return False

class Streamer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.outputs = []
        self.httpd = http.server.ThreadingHTTPServer(('0.0.0.0', 8080), requestHandler)
        self.httpd.still = None
        self.httpd.motion = None
        self.start()
    
    def run(self):
        self.httpd.serve_forever()
        
    def done(self):
        print('Streamer exiting')
        self.httpd.shutdown()
        for output in mjpeg_outputs:
            output.running = False
            output.event.set()

class FrameHandler:
    def __init__(self):
        self.frames = []

    def write(self, buf):
        print(datetime.datetime())
        self.frames.append(buf)

def mergeConfig(o):
    global settings
    for key in o:
        settings[key] = o[key]
    with open('config.json', 'w') as f:
        json.dump(settings, f)
        f.close()


running = True
def signal_handler(sig, frame):
    global running
    running = False
    print('Exiting ...')
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


if os.path.isfile('config.json'):
    with open('config.json', 'r') as f:
        settings = json.load(f)
        f.close()


with picamera.PiCamera() as camera:
    camera.resolution = (settings['width'], settings['height'])
    camera.framerate = settings['fps']
    camera.annotate_background = picamera.Color(y=0, u=0, v=0)

    motionDetection = MotionDetection(camera, settings)
    streamer = Streamer()

    stream = picamera.PiCameraCircularIO(camera, seconds=2)
    camera.start_recording(stream, format='h264', bitrate=0, quality=20)
    while running:
        try:
            camera.wait_recording(0.1) #settings['secondsBetweenDetection'])
        except picamera.PiCameraError as e:
            print('Exception while recording to circular buffer')
            print(str(e))
            break

        if motionDetection.detect(camera, continuous=True):
            print('Motion detected!')
            # As soon as we detect motion, split the recording to
            # record the frames "after" motion
            filename = datetime.datetime.fromtimestamp(motionDetection.motionAtTimestamp).strftime('%Y%m%d%H%M%S_%%dx%%dx%%d') % (settings['width'], settings['height'], settings['fps'])   
            subfolder = 'h264/' + filename[0:8]
            pathlib.Path(subfolder).mkdir(parents=True, exist_ok=True)

            camera.split_recording('%s/%s_after.h264' % (subfolder, filename))

            # Wait until motion is no longer detected, then split
            # recording back to the in-memory circular buffer
            while motionDetection.detect(camera, continuous=True):
                if running == False:
                    break
                # check for motion every second while we're recording to h264
                try:
                    camera.wait_recording(0.1) #settings['secondsBetweenDetection'])
                except picamera.PiCameraError as e:
                    print('Exception while recording to h264 file')
                    print(str(e))
                    # Unsure how to handle full disk
                    break
            print('Motion stopped!')

            # Write the frames from "before" motion to disk as well
            stream.copy_to('%s/%s_before.h264' % (subfolder, filename), seconds=2)
            stream.clear()
            camera.split_recording(stream)
    camera.stop_recording()

streamer.done()
