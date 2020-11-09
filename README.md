# Welcome

This is a python script to convert a Raspberry Pi 3 (or newer) into a motion detection surveillance system.

With a raspi4 it can check for motion 3 times a second, output 1088p (not a typo) 30fps h264 files, all while keeping the CPU on a single core under 80%! This is a huge improvement over the previous version of this script. Hooray for picamera!

It also serves a simple web UI (runs on port 8080) that allows you to watch the stream and see whether motion was detected. See a demo of the previous version here: https://www.youtube.com/watch?v=56VteHCRxhc

I need to create a new post for the latest version, but you can ready about my journey to version 1 here: https://blog.alanszlosek.com/post/2019/11/raspi-hd-motion-detection-encoding-streaming/

# Features

* Motion detection
* Region exclusion
* Encoding to h264
* Live streaming
* 1088p at 30fps on a raspi4

# Installation

It's been a while since I installed from scratch. Need to try these steps again:

```
# Option 1: use the python3-opencv deb package from the repos
apt install python3-opencv

# Option 2, if you encounter issues or memory leaks: use the newer opencv-python version from pip
apt install python3-pip python3-picamera python3-numpy libopenjp2-7 libtiff5 libwebp6 libilmbase23 libopenexr23 libavcodec58 libswscale5 libavformat58 libgtk-3-0 libgtk-3-bin libgtk-3-common libatlas3-base
# you can run this as the user you'll be running main.py as
pip3 install opencv-python

# If you've already enabled the camera via raspi-config you can skip this:
modprobe bcm2835-v4l2
# add that module to /etc/modules-load.d/modules.conf
```

Then run: `python3 main.py`

See `etc/systemd/surveillance.service` if you want to run on boot via systemd. Copy the service file to `/etc/systemd/system`, then run `systemctl daemon-reload` then `systemd enable surveillance`.

Visit the Raspberry Pi's IP address on port 8080 to view the web UI.

# Usage

It's not very user-friendly yet.

`Ctrl-C` will make it shutdown gracefully.

Look for `width`, `height` and `fps` variables in `main.py`. Line 38. Edit to your liking.

# Building out an end to end surveillance system

Admittedly, capturing videos on a single raspi is not very helpful. I haven't finalized the tooling yet, but here's what my system currently looks like:

1. h264 videos are stored on raspi
1. Every night, they are SCPed to an Ubuntu machine, where they are then processed like so:
  1. "before" and "after" h264 files are concatted together using ffmpeg into an mp4. This is a quick operation, since no transcoding is needed. We just wrap an mp4 container around the h264 frames.
  1. They are "indexed" into a sqlite3 database
  1. They are run through tensorflow object detection and each file is tagged in the database with the objects that were detected. We also capture stills during object detection so we can see the first frame that contains a detected object.
1. I can use a simple webapp to quickly browse videos by tags, and easily cycle through videos using keyboard shortcuts

I'm working on a unified dashboard that can show live streams from more than 1 camera at a time. It'll be simple: each node broadcasts itself via UDP to a central server. The server keeps inventory, and serves a simple HTML page with a grid for each camera node that's running.

I hope to release all of this tooling eventually.

# Future Thoughts

I'm curious whether we can use h264 instead of jpeg. Perhaps we can then simply ask ffmpeg to add an mp4 container.
https://support.mozilla.org/en-US/kb/html5-audio-and-video-firefox#w_supported-formats
