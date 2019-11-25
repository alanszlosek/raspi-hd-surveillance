
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

# Miscellaneous Notes

check ffmpeg codecs for h264 and omx for hardware accelerated encoding on the raspi:

`ffmpeg -codecs | grep h264`

See options for the hardware encoder, there aren't many!

`ffmpeg -h encoder=h264_omx`

# Future Thoughts

I'm curious whether we can use h264 instead of jpeg. Perhaps we can then simply ask ffmpeg to add an mp4 container.
https://support.mozilla.org/en-US/kb/html5-audio-and-video-firefox#w_supported-formats
