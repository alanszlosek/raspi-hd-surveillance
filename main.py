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

# Hi! Use this code to turn your Raspberry Pi into a surveillance camera.
# It records h264 videos when motion is detected
# It also contains a simple webpage where you can watch the live stream via JPEGs that refresh twice a second
# 
# Regarding the output video, you'll need to join the before and after files together files into a mp4 to view them. You can use ffmpeg to do this:
# ffmpeg -framerate 10 -i "concat:20201025060604_before.h264|20201025060604_after.h264" -c:v copy 20201025060604.mp4 

# A raspi4 can handle 1088p () 30fps and detect motion 2-3 times per second, while keeping CPU core around 80%!

# Default settings. If config.json is present, we'll merge in those values on start.
settings = {
    # raspi4 settings
    'fps': 30,
    'width': 1920,
    'height': 1088,
    # raspi zero settings (IIRC)
    #'fps': 30,
    #'width': 1280,
    #'height': 720,

    'sensitivityPercentage': 0.2,
    # Check for motion at this interval. 0.3 (three times a second) is often frequent enough to pick up cars on a residential road, but it depends on many things. You'll need to fiddle.
    'secondsBetweenDetection': 0.3,
    'ignore': [
        # [startX, startY, endX, endY]
    ]
}


class SplitFrames(object):
    def __init__(self):
        self.buf = None

    def write(self, buf):
        if not buf.startswith(b'\xff\xd8'):
            print('ERROR: buffer with JPEG data does not start with magic bytes')

        # NOTE: Until i see "buffer does not start with magic bytes" actually happen, let's just use the buffer picamera gives us instead of copying into a BytesIO stream
        self.buf = buf

class MotionDetection(threading.Thread):
    def __init__(self, camera, settings):
        threading.Thread.__init__(self)
        self.running = True
        self.camera = camera
        self.settings = settings

        self.previousFrame = None
        self.motionDetected = False
        self.motionAtTimestamp = 0
        self.checkAfterTimestamp = 0
        self.updateDetectStillAfterTimestamp = 0
        self.stopRecordingAfterTimestamp = 0
        self.stopRecordingAfterTimestampDelta = 2

        # TODO: re-try capturing straight to bgr instead of jpeg. it'll likely be slower since there will be more data to copy from the camera, and because we'll then have to encode to JPEG for the live stream. but worth another test.

        # for capturing to bgr
        #self.buffer = numpy.empty( (self.settings['width'] * self.settings['height'] * 3,), dtype=numpy.uint8)
        # set once ... since we're re-using the same buffer, we don't have to set it ever again
        #self.buffer.shape = (self.settings['height'], self.settings['width'], 3)

        # Create ndarrays ahead of time to reduce memory operations and GC
        self.buffer = SplitFrames()
        self.decoded = numpy.empty( (self.settings['height'], self.settings['width'], 3), dtype=numpy.uint8)
        self.grayscale = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.previous = None
        self.diff = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.threshold = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.ignore = numpy.ones( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.scratch = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)

        self.config(settings)

        self.start()

    def config(self, settings):
        self.sensitivityPercentage = self.settings['sensitivityPercentage'] / 100

        # 0.2% of white pixels signals motion
        cutoff = math.floor(self.settings['width'] * self.settings['height'] * self.sensitivityPercentage)
        # Pixels with motion will have a value of 255
        # Sum the % of pixels having value of 255 to 
        self.cutoff = cutoff * 255

        # Assemble an ndarray of our ignore regions. We'll multiply this by our current frame to zero-out pixels we want to ignore
        for region in self.settings['ignore']:
            x = region[0]
            y = region[1]
            while y < region[3]:
                self.ignore[y, x:region[2]] = 0
                y += 1


    def run(self):
        global streamer
        self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        t = time.time()
        cutoff = t + self.settings['secondsBetweenDetection']
        for foo in self.camera.capture_continuous(self.buffer, format='jpeg', use_video_port=True, quality=100):
            if self.running == False:
                break

            streamer.httpd.still = self.buffer.buf

            t = time.time()
            if t > cutoff:
                # detection
                #print(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'))
                self._detect(t)
                cutoff = t + self.settings['secondsBetweenDetection']
            self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _detect(self, currentFrameTimestamp):
        img_np = numpy.frombuffer(self.buffer.buf, dtype=numpy.uint8)
        self.decoded = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
        cv2.cvtColor(self.decoded, cv2.COLOR_BGR2GRAY, self.grayscale)

        if self.previous is None:
            self.previous = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
            numpy.copyto(self.previous, self.grayscale)
            return False

        cv2.absdiff(self.previous, self.grayscale, dst=self.diff)
        numpy.multiply(self.ignore, self.diff, out=self.scratch)
        # rely on numpy to ignore certain portions of the frame by multiplying those pixels by 0
        cv2.threshold(self.scratch, 25, 255, cv2.THRESH_BINARY, self.threshold)

        # Add up all pixels. Pixels with motion will have a value of 255
        pixelSum = numpy.sum(self.threshold)
        if pixelSum > self.cutoff: # motion detected in frame
            # Log that we are seeing motion
            self.motionDetected = True
            self.motionAtTimestamp = currentFrameTimestamp
            # Stop recording after 10 seconds of no motion
            self.stopRecordingAfterTimestamp = currentFrameTimestamp + self.stopRecordingAfterTimestampDelta                        
            print('Seeing motion. Will stop recording after %s' % str(self.stopRecordingAfterTimestamp))

            # Let's only use the current frame for detection if it contains motion.
            # The thought is that we want to detect very slow moving objects ... objects that might not trigger 2% of pixel changes within 1/3 second but that might over a longer time frame.
            numpy.copyto(self.previous, self.grayscale)
        # End conditional frame comparison logic

        if self.motionDetected and self.stopRecordingAfterTimestamp < currentFrameTimestamp:
            # Tell writer we haven't seen motion for a while
            print("%d seconds without motion" % self.stopRecordingAfterTimestampDelta)

            # Commented out the following so we preserve the timestamp of last motion
            #self.motionAtTimestamp = 0
            # Log that we are no longer seeing motion
            self.motionDetected = False


    def done(self):
        print('MotionDetection exiting')
        self.running = False


class requestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Suppress the default behavior of logging every incoming HTTP request to stdout
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
                print('BrokenPipeError')
            except ConnectionResetError as e:
                print('ConnectionResetError')

        else:
            # TODO: return 404
            return False

class Streamer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.outputs = []
        self.httpd = http.server.HTTPServer(('0.0.0.0', 8080), requestHandler)
        self.httpd.still = None
        self.start()
    
    def run(self):
        self.httpd.serve_forever()
        
    def done(self):
        print('Streamer exiting')
        self.httpd.shutdown()


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

# Merge in settings from config.json
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
    camera.start_recording(stream, format='h264')
    while running:
        try:
            camera.wait_recording(0.1)
        except picamera.PiCameraError as e:
            print('Exception while recording to circular buffer')
            print(str(e))
            break

        if motionDetection.motionDetected:
            print('Motion detected!')
            # As soon as we detect motion, split and start recording to h264
            # We'll save the circular buffer to h264 later, since it contains "before motion detected" frames
            filename = datetime.datetime.fromtimestamp(motionDetection.motionAtTimestamp).strftime('%Y%m%d%H%M%S_%%dx%%dx%%d') % (settings['width'], settings['height'], settings['fps'])   
            subfolder = 'h264/' + filename[0:8]
            pathlib.Path(subfolder).mkdir(parents=True, exist_ok=True)

            camera.split_recording('%s/%s_after.h264' % (subfolder, filename))

            # Wait until motion is no longer detected, then split recording back to the in-memory circular buffer
            while motionDetection.motionDetected:
                if running == False:
                    break
                try:
                    camera.wait_recording(0.1)
                except picamera.PiCameraError as e:
                    print('Exception while recording to h264 file')
                    print(str(e))
                    # TODO: Unsure how to handle full disk
                    break
            print('Motion stopped!')

            # Write the frames from "before" motion to disk as well
            stream.copy_to('%s/%s_before.h264' % (subfolder, filename))
            stream.clear()
            camera.split_recording(stream)
    streamer.done()
    motionDetection.done()
    # TODO: find the proper way to wait for threads to terminate
    time.sleep(3)
    camera.stop_recording()

