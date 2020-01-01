
# Installation

It's been a while since I installed from scratch. Need to try these steps again:

```
apt-get install python3-pip python3-picamera
pip3 install opencv-python
# numpy imutils?

modprobe bcm2835-v4l2
# add that module to /etc/modules-load.d/modules.conf
```

## Running standalone

Run: `python3 main.py`

## Running as a daemon

1. Copy the surveillance.service systemd unit file somewhere ... TODO: check on the recommended location for raspbian
1. `systemctl daemon-reload` - to pick up the unit file
1. `systemctl enable surveillance` - to enable the service at boot
1. `systemctl start surveillance` - to run the daemon now


# Usage

It's not very user-friendly yet.

`Ctrl-C` will make it shutdown gracefully.

Look for `width`, `height` and `fps` variables in `main.py`. Line 377. Edit to your liking.

# Miscellaneous Notes

check ffmpeg codecs for h264 and omx for hardware accelerated encoding on the raspi:

`ffmpeg -codecs | grep h264`

See options for the hardware encoder, there aren't many!

`ffmpeg -h encoder=h264_omx`

# Future Thoughts

I'm curious whether we can use h264 instead of jpeg. Perhaps we can then simply ask ffmpeg to add an mp4 container.
https://support.mozilla.org/en-US/kb/html5-audio-and-video-firefox#w_supported-formats
