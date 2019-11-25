import cv2
import datetime
import http.server
import io
import math
import numpy
import signal
from socketserver import ThreadingMixIn
import subprocess
import time
import threading
from picamera import PiCamera


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
    def __init__(self, fps, width, height):
        threading.Thread.__init__(self)

        self.running = True
        self.event = threading.Event()
        self.fps = fps
        self.width = width
        self.height = height

        self.frames = []

        self.start()
    
    def run(self):
        width = self.width
        height = self.height
        fps = self.fps
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
                    print("Saving to %s" % filename)
                    # Use the Raspberry Pi's built-in h264 hardware encoder
                    # Struggles to keep up with 1080p@10
                    args = ['ffmpeg', '-s', '%dx%d' % (width, height), '-f', 'image2pipe', '-framerate', str(fps), '-i', 'pipe:0', '-c:v', 'h264_omx', '-b:v', '2000k', filename]
                    # Neither does lower bitrate help with 1080p@10
                    args = ['ffmpeg', '-s', '%dx%d' % (width, height), '-f', 'image2pipe', '-framerate', str(fps), '-i', 'pipe:0', '-c:v', 'h264_omx', '-b:v', '400k', filename]

                    # Or multiple threads and the standard h264 so we can tune crf and use a preset
                    args = ['ffmpeg', '-s', '%dx%d' % (width, height), '-f', 'image2pipe', '-framerate', str(fps), '-i', 'pipe:0', '-c:v', 'h264', '-crf', '18', '-preset', 'ultrafast', '-threads', '3', filename]
                    # This works well for 1080p@10, and can almost keep up at 15fps
                    args = ['ffmpeg', '-s', '%dx%d' % (width, height), '-f', 'image2pipe', '-framerate', str(fps), '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '23', '-preset', 'ultrafast', '-movflags', '+faststart', '-threads', '3', filename]

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
    def __init__(self, fps, width, height, frameHandler):
        threading.Thread.__init__(self)

        self.running = True
        self.event = threading.Event()
        self.fps = fps
        self.frameHandler = frameHandler
        self.width = width
        self.height = height

        self.toVideo = ToVideo(fps, width, height)

        self.start()
    
    def run(self):
        width = self.width
        height = self.height
        fps = self.fps

        previousFrame = None
        currentFrame = None
        numpyFrame = None

        # 1% of while pixels signals motion
        cutoff = math.floor(width * height * 0.01)
        # Pixels with motion will have a value of 255
        # Sum of 1% of pixels having value of 255 is ...
        cutoff = cutoff * 255

        #preview = time.perf_counter()

        # millisecond timestamp of when we last compared frames for motion
        # why not just keep an integer of all the frames i've seen? and compare that way ... no timestamps involved
        lastCheckedFrame = 0
        frameCounter = 0
        motionAtFrame = 0
        stopAfter = 10 * fps # stop recording after 10 seconds of frames of no motion

        while self.running == True:
            # Wait until we get the signal that there is work to do
            # This may be frames present, or cleanup work
            self.event.wait()
            # clear the event right away since we're going to fetch all frames needing processing
            self.event.clear()

            currentFrame = self.frameHandler.getFrame()
            while currentFrame:
                frameCounter += 1

                # Adjust frame timestamp ... is this too cpu intensive?
                self.stampPrefix = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ')

                # TODO: Only compare frames every fps frames
                if frameCounter - lastCheckedFrame >= self.fps:
                    # Copy into numpy/opencv so we can do motion detection
                    img_np = numpy.frombuffer(currentFrame, dtype=numpy.uint8)
                    numpyFrame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)

                    #profile.begin('grayscale-diff-threshold')
                    grayscale = cv2.cvtColor(numpyFrame, cv2.COLOR_RGB2GRAY)
                    # on our first run remember the first frame and skip motion detection
                    if previousFrame is None:
                        previousFrame = grayscale
                        currentFrame = self.frameHandler.getFrame()
                        continue
                    frameDiff = cv2.absdiff(previousFrame, grayscale)
                    frameThreshold = cv2.threshold(frameDiff, 25, 255, cv2.THRESH_BINARY)[1]
                    #profile.end('grayscale-diff-threshold')

                    #profile.begin('motion-check')
                    # Add up all pixels. Pixels with motion will have a value of 255
                    pixelSum = numpy.sum(frameThreshold)
                    if pixelSum > cutoff: # motion detected in frame
                        # Tell writer the timestamp if we've detected new motion
                        if motionAtFrame == 0:
                            motionAtFrame = frameCounter
                            ts = time.strftime('%Y%m%d%H%M%S') + ('_%dx%d_%d' % (width, height, fps))
                            self.toVideo.frames.append(ts)
                            self.toVideo.event.set()
                        else:
                            # Log when we've seen continued motion
                            motionAtFrame = frameCounter
                    # Use current frame in next comparison
                    previousFrame = grayscale
                    lastCheckedFrame = frameCounter
                    #profile.end('motion-check')
                # End conditional frame comparison logic

                if motionAtFrame > 0:
                    framesSinceMotion = frameCounter - motionAtFrame
                    if framesSinceMotion > stopAfter:
                        # Tell writer we haven't seen motion for a while
                        print("%d seconds without motion" % (framesSinceMotion / fps))

                        self.toVideo.frames.append(False)
                        self.toVideo.event.set()
                        motionAtFrame = 0
                    else:
                        self.toVideo.frames.append(currentFrame)
                        self.toVideo.event.set()
                currentFrame = self.frameHandler.getFrame()

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
        if self.path == '/':
            self.event = threading.Event()
            self.running = True
            self.frame = None

            # send headers
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=PICAM_MJPEG')
            self.end_headers()

            mjpeg_outputs.append(self)

            while self.running:
                self.event.wait()
                self.event.clear()
                if self.frame:
                    # this doesn't seem to work
                    if self.wfile.closed or not self.wfile.writable():
                        break
                    try:
                        self.wfile.write(b'--PICAM_MJPEG\nContent-Type: image/jpeg\n\n')
                        self.wfile.write(self.frame)
                    except BrokenPipeError as e:
                        break
            mjpeg_outputs.remove(self)

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
    def __init__(self, camera, fps, width, height):
        self.camera = camera
        self.fps = fps
        self.width = width
        self.height = height
        self.motionDetection = None
        self.maxFrame = fps
        self.frameCount = self.maxFrame
        self.stampPrefix = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ')
        self.unixtime = float(int(time.time())) + 1.0
        self.frames = []
        self.output = bytes()

        self.unixtime = float( int(time.time()) )
        self.mjpeg = True

    def getFrame(self):
        if len(self.frames) > 0:
            # Return first frame in queue
            return self.frames.pop(0)
        return None

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # TODO: this might not be the right place to calculate stamp for next frame
            # not sure how else to do it
            unixtime = time.time()
            if unixtime > self.unixtime:
                if self.frameCount < self.maxFrame:
                    print('Fell short of fps: %d vs %d' %(self.frameCount, self.maxFrame))
                self.stampPrefix = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ')
                self.frameCount = 1
                self.unixtime = 1.0 + int(unixtime)
            else:
                self.frameCount += 1
            self.camera.annotate_text = self.stampPrefix + ("%2d" % self.frameCount)

            # Start of new frame; close the old one (if any) and open a new output
            self.frames.append( self.output )
            self.motionDetection.event.set()

            if self.mjpeg:
                for output in mjpeg_outputs:
                    output.frame = self.output
                    output.event.set()
                self.mjpeg = False 
            else:
                self.mjpeg = True

            self.output = bytes()
        else:
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

fps = 10
width = 1920
height = 1080

fps = 20
width = 1280
height = 720

# using the h264 encoder with three threads we can use crf, the ultrafast preset, and handle 1080p@10 quite well. it keeps up and memory doesn't keep growing
# usnig the hardware encoder, it can't keep up at 800k or 2000k bitrate, and there's no option for multiple threads since it's hardware. doesn't keep up


# start threads for motion detection


#camera = PiCamera(resolution=(width,height), framerate=fps) #, sensor_mode=3)  #, sensor_mode=1)
with PiCamera(resolution=(width,height), framerate=fps) as camera: #, sensor_mode=3)  #, sensor_mode=1)
#with PiCamera(resolution='VGA') as camera: #, sensor_mode=3)  #, sensor_mode=1)
    camera.start_preview()
    time.sleep(2)

    streamer = Streamer()
    frameHandler = FrameHandler(camera, fps, width, height)
    motionDetection = MotionDetection(fps, width, height, frameHandler)
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

        i += 1
        if i > 10:
            print("%s @%d mode: %d shutter: %f exposure: %f" % (camera.resolution, camera.framerate, camera.sensor_mode, camera.shutter_speed, camera.exposure_speed))
            i = 0

    camera.stop_recording()
    camera.close()


# close out threads
motionDetection.done()
motionDetection.event.set()
streamer.done()
    
