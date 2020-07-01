# Welcome

This is a python script to convert a Raspberry Pi 3 (or newer) into a motion detection surveillance system.

It creates 1280x720 20fps mp4 videos whenever it detects motion, and saves them locally. 

I recently created a simple web UI (runs on port 8080) that allows you to watch the stream and see whether motion was detected. See a demo video here: https://www.youtube.com/watch?v=56VteHCRxhc

If you want to read about my journey, I wrote a blog post here: https://blog.alanszlosek.com/post/2019/11/raspi-hd-motion-detection-encoding-streaming/

# Features

* Motion detection
* Region exclusion
* Encoding to MP4
* Live streaming
* All in 720p at 20fps

# Installation

It's been a while since I installed from scratch. Need to try these steps again:

```
apt-get install python3-pip python3-picamera
pip3 install opencv-python
# numpy imutils?

modprobe bcm2835-v4l2
# add that module to /etc/modules-load.d/modules.conf
```

Then run: `python3 main.py`

See `etc/systemd/surveillance.service` if you want to run on boot via systemd.

Visit the Raspberry Pi's IP address on port 8080 to view the web UI.

# Usage

It's not very user-friendly yet.

`Ctrl-C` will make it shutdown gracefully.

Look for `width`, `height` and `fps` variables in `main.py`. Line 709. Edit to your liking.

# Miscellaneous Notes

check ffmpeg codecs for h264 and omx for hardware accelerated encoding on the raspi:

`ffmpeg -codecs | grep h264`

See options for the hardware encoder, there aren't many!

`ffmpeg -h encoder=h264_omx`

# Future Thoughts

I'm curious whether we can use h264 instead of jpeg. Perhaps we can then simply ask ffmpeg to add an mp4 container.
https://support.mozilla.org/en-US/kb/html5-audio-and-video-firefox#w_supported-formats
