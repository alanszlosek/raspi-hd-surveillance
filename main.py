import cv2
import datetime
import gpiozero
import http.server
import json
import math
import numpy
import os
import pathlib
import picamera
import requests
import signal
import socket
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
    # how many seconds of h264 to save prior to when motion is detected. this will be saved in a *_before.h264 file
    'secondsToSaveBeforeMotion': 2,
    'secondsToSaveAfterMotion': 2,
    'heartbeatServer': '192.168.1.173',
    'heartbeatPort': 5001,
    'ignore': [
        # [startX, startY, endX, endY]
        [0, 0, 1920, 669],
        [0, 808, 1920, 1088]
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

class MotionDetection:
    def __init__(self, camera, settings, streamer):
        self.camera = camera
        self.settings = settings

        self.previousFrame = None
        self.motionDetected = False
        self.motionAtTimestamp = 0
        self.checkAfterTimestamp = 0
        self.updateDetectStillAfterTimestamp = 0
        self.stopRecordingAfterTimestamp = 0
        self.stopRecordingAfterTimestampDelta = settings['secondsToSaveAfterMotion']

        # Create ndarrays ahead of time to reduce memory operations and GC
        self.decoded = numpy.empty( (self.settings['height'], self.settings['width'], 3), dtype=numpy.uint8)
        streamer.httpd.still = self.decoded
        self.grayscale = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.previous = None
        self.diff = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.threshold = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.ignore = numpy.ones( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)
        self.scratch = numpy.empty( (self.settings['height'], self.settings['width']), dtype=numpy.uint8)

        self.config(settings)

    def config(self, settings):
        self.sensitivityPercentage = self.settings['sensitivityPercentage'] / 100

        # N% of white pixels signals motion
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
    
    def check(self):
        global streamer
        self.camera.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            # TODO: capture into a buffer not shared with the http streamer ...
            # as-is we can have race-conditions
            self.camera.capture(self.decoded, format='bgr', use_video_port=True)
            t = time.time()
            print('Checking for motion')
            self._detect(t)

        except Exception as e:
            print('Exception within capture_continuous, bailing')
            print(str(e))

    def _detect(self, currentFrameTimestamp):
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

            # TODO: race condition alert. should not use a buffer that's being actively used by MotionDetection
            still = cv2.imencode('.jpg', self.server.still)[1]

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

class Periodic(threading.Thread):
    def __init__(self, settings):
        threading.Thread.__init__(self)
        self.settings = settings
        self.running = True
        self.start()

    def done(self):
        self.running = False

class Heartbeat(Periodic):
    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            sock.sendto(b"hi", (self.settings['heartbeatServer'], self.settings['heartbeatPort']))
            time.sleep(2)

class Temperature(Periodic):
    def run(self):
        influx_url = 'http://%s:8086/write' % (self.settings['heartbeatServer'],)
        while self.running:
            cpu = gpiozero.CPUTemperature()
            s = 'raspi.temperature_celsius,host=%s value=%f %d' % (socket.gethostname(), math.floor(cpu.temperature), time.time_ns())
            r = requests.post(influx_url, params={'db': 'cube'}, data=s)
            time.sleep(30)


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

    heartbeat = Heartbeat(settings)
    temperature = Temperature(settings)
    streamer = Streamer()
    motionDetection = MotionDetection(camera, settings, streamer)

    # See stream.copy_to() usage below for why I'm creating a larher buffer
    stream = picamera.PiCameraCircularIO(camera, seconds = settings['secondsToSaveBeforeMotion'] * 2)
    camera.start_recording(stream, format='h264')
    while running:
        try:
            # Need a better way to do this, based on how long capture() actually/usually takes
            # Hardcoded this to 0.1 for now, since capture() is slow and I want detection 3x per second
            camera.wait_recording(0.1) #settings['secondsBetweenDetection'])
        except picamera.PiCameraError as e:
            print('Exception while recording to circular buffer')
            print(str(e))
            break
        except Exception as e:
            print('Non PiCamera exception while recording to circular buffer')
            print(str(e))
            break
    
        # TODO: return boolean from check instead of reaching into motionDetection.motionDetected
        motionDetection.check()
    
        if motionDetection.motionDetected:
            print('Motion detected!')
            # As soon as we detect motion, split and start recording to h264
            # We'll save the circular buffer to h264 later, since it contains "before motion detected" frames
            filename = datetime.datetime.fromtimestamp(motionDetection.motionAtTimestamp).strftime('%Y%m%d%H%M%S_%%dx%%dx%%d') % (settings['width'], settings['height'], settings['fps'])   
            subfolder = 'h264/' + filename[0:8]
            pathlib.Path(subfolder).mkdir(parents=True, exist_ok=True)

            try:
                camera.split_recording('%s/%s_after.h264' % (subfolder, filename))
            except picamera.PiCameraError as e:
                print('Exception while calling split_recording')
                print(str(e))
                break
            except Exception as e:
                print('Non PiCamera exception while calling split_recording')
                print(str(e))
                break

            # Wait until motion is no longer detected, then split recording back to the in-memory circular buffer
            while motionDetection.motionDetected:
                if running == False:
                    break
                try:
                    camera.wait_recording(1.0)
                except picamera.PiCameraError as e:
                    print('Exception while recording to h264 file')
                    print(str(e))
                    # TODO: Unsure how to handle full disk
                    break
                except Exception as e:
                    print('Non PiCamera exception while calling split_recording')
                    print(str(e))
                    break
                motionDetection.check()
            print('Motion stopped!')

            # Write the frames from "before" motion to disk as well
            try:
                # The reason I'm explicitly specifying seconds here is that according to the documentation,
                # even if you create a circular buffer to hold 2 seconds, that's the lower bound. It might hold more
                # depending on how much has changed between frames. Sounds like it allocates by bitrate behind the scenes,
                # and truncates based on bytes within the buffer. So if some frames have less data it'll be able to pack more into the buffer
                stream.copy_to('%s/%s_before.h264' % (subfolder, filename), seconds = settings['secondsToSaveBeforeMotion'])
            except Exception as e:
                print('Exception while calling copy_to')
                print(str(e))
                break
            stream.clear()

            try:
                camera.split_recording(stream)
            except picamera.PiCameraError as e:
                print('Exception while calling split_recording (2)')
                print(str(e))
                break
            except Exception as e:
                print('Non PiCamera exception while calling split_recording (2)')
                print(str(e))
                break
    heartbeat.done()
    temperature.done()
    streamer.done()
    # TODO: find the proper way to wait for threads to terminate
    time.sleep(3)
    camera.stop_recording()

