import datetime
import math
import numpy as np
import os
import pathlib
import re
import six.moves.urllib as urllib
import sys
import tarfile
import tensorflow as tf
import time
import zipfile
import cv2
import sqlite3

from collections import defaultdict
from io import StringIO
from matplotlib import pyplot as plt
from PIL import Image
from object_detection.utils import label_map_util
from object_detection.utils import visualization_utils as vis_util


# open sqlite3 database for indexing
db = sqlite3.connect('/home/user/Documents/surveillance-videos/files.sqlite3')
c = db.cursor()

# TODO: test that cap didn't fail

# What model to download.
# Models can bee found here: https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/detection_model_zoo.md
MODEL_NAME = 'ssd_inception_v2_coco_2017_11_17'
MODEL_FILE = MODEL_NAME + '.tar.gz'
DOWNLOAD_BASE = 'http://download.tensorflow.org/models/object_detection/'

# Path to frozen detection graph. This is the actual model that is used for the object detection.
PATH_TO_CKPT = MODEL_NAME + '/frozen_inference_graph.pb'

# List of the strings that is used to add correct label for each box.
PATH_TO_LABELS = os.path.join('data', 'mscoco_label_map.pbtxt')

# Number of classes to detect
NUM_CLASSES = 90


# Download Model
if not os.path.exists(os.path.join(os.getcwd(), MODEL_FILE)):
    print("Downloading model")
    opener = urllib.request.URLopener()
    opener.retrieve(DOWNLOAD_BASE + MODEL_FILE, MODEL_FILE)
    tar_file = tarfile.open(MODEL_FILE)
    for file in tar_file.getmembers():
        file_name = os.path.basename(file.name)
        if 'frozen_inference_graph.pb' in file_name:
            tar_file.extract(file, os.getcwd())


# Load a (frozen) Tensorflow model into memory.
detection_graph = tf.Graph()
with detection_graph.as_default():
    od_graph_def = tf.compat.v1.GraphDef()
    with tf.io.gfile.GFile(PATH_TO_CKPT, 'rb') as fid:
        serialized_graph = fid.read()
        od_graph_def.ParseFromString(serialized_graph)
        tf.import_graph_def(od_graph_def, name='')


# Loading label map
# Label maps map indices to category names, so that when our convolution network predicts `5`, we know that this corresponds to `airplane`.  Here we use internal utility functions, but anything that returns a dictionary mapping integers to appropriate string labels would be fine
label_map = label_map_util.load_labelmap(PATH_TO_LABELS)
categories = label_map_util.convert_label_map_to_categories(
    label_map, max_num_classes=NUM_CLASSES, use_display_name=True)
category_index = label_map_util.create_category_index(categories)


# Helper code
#def load_image_into_numpy_array(image):
#    (im_width, im_height) = image.size
#    return np.array(image.getdata()).reshape(
#        (im_height, im_width, 3)).astype(np.uint8)

snafu = datetime.datetime.utcnow()
snafu = snafu - datetime.timedelta(hours=9)

# Detection
with detection_graph.as_default():


    # fetch all videos from database
    c = db.cursor()
    c.execute("SELECT l.path,l.sha1 FROM locations AS l WHERE objectDetectionRanAt = 0 AND fileCreatedAt >= strftime('%s', '2020-06-20') ORDER by fileCreatedAt DESC")
    #c.execute("SELECT l.path,l.sha1 FROM locations AS l WHERE fileCreatedAt >= strftime('%s', '2020-06-20') AND fileCreatedAt <= strftime('%s', '2020-07-11 18:00:00') ORDER by fileCreatedAt DESC")
    files = c.fetchall()
    for row in files:
        fileSha1 = row[1]

        st = time.time()

        print('')
        print('==== ==== ====')
        print('')

        # Define the video stream
        #cap = cv2.VideoCapture(0)  # Change only if you have more than one webcams

        # TODO: parse fps from filename
        dims = re.search(r'(\d+)x(\d+)x(\d+)', row[0])
        if dims:
            fps = int(dims.group(3))
            skip = math.floor(fps / 4)
        else:
            skip = math.floor(20 / 4)

        cap = cv2.VideoCapture(row[0])
        if not cap:
            print('Failed to open %s' % (row[0],))
            break
        print('Opened and processing: %s' % row[0])
        final_classes = set()

        with tf.compat.v1.Session(graph=detection_graph) as sess:
            #skipFrame = False
            frameCount = 0
            while True:
                # Read frame from camera
                ret, image_np = cap.read()
                if ret == False:
                    print('Done')
                    cv2.destroyAllWindows()
                    break

                frameCount += 1
                if frameCount < skip:
                    continue
                else:
                    frameCount = 0


                # Expand dimensions since the model expects images to have shape: [1, None, None, 3]
                image_np_expanded = np.expand_dims(image_np, axis=0)
                # Extract image tensor
                image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')
                # Extract detection boxes
                boxes = detection_graph.get_tensor_by_name('detection_boxes:0')
                # Extract detection scores
                scores = detection_graph.get_tensor_by_name('detection_scores:0')
                # Extract detection classes
                classes = detection_graph.get_tensor_by_name('detection_classes:0')
                # Extract number of detectionsd
                num_detections = detection_graph.get_tensor_by_name(
                    'num_detections:0')
                # Actual detection.
                (boxes, scores, classes, num_detections) = sess.run(
                    [boxes, scores, classes, num_detections],
                    feed_dict={image_tensor: image_np_expanded})
                # Visualization of the results of a detection.
                vis_util.visualize_boxes_and_labels_on_image_array(
                    image_np,
                    np.squeeze(boxes),
                    np.squeeze(classes).astype(np.int32),
                    np.squeeze(scores),
                    category_index,
                    use_normalized_coordinates=True,
                    line_thickness=8)

                p = pathlib.PurePath(row[0])
                i = 0
                while i < len(classes[0]):
                    tfClassId = classes[0][i]
                    if scores[0][i] > 0.70:
                        className = category_index.get( tfClassId )['name']
                        # if not already in final_classes, save snapshot of frame with detection
                        if className not in final_classes:
                            # save image_np
                            imagePath = "%s/%s_%s.jpg" % (p.parent, p.stem, className)
                            cv2.imwrite(imagePath, image_np)

                            final_classes.add(className)
                    i = i + 1

                # Display output
                #cv2.imshow('object detection', cv2.resize(image_np, (1024, 768))) #800, 600)))

                #if cv2.waitKey(25) & 0xFF == ord('q'):
                #    cv2.destroyAllWindows()
                #    break
            # end frame loop

        # tag video with any classes we found
        print(final_classes)

        d = db.cursor()

        for tfClass in final_classes:
            # make sure tag exists in tags table
            d.execute('SELECT id FROM tags WHERE tag=?', (tfClass,))
            tag = d.fetchone()
            if tag:
                tagId = tag[0]
            else:
                print('Tag not found, pre-creating: %s' % (tfClass,))
                d.execute('INSERT INTO tags (tag) VALUES(?)', (tfClass,))
                tagId = d.lastrowid

            d.execute('REPLACE INTO file_tag (tagId,fileSha1,taggedBy) VALUES(?,?,?)', (tagId,fileSha1,2))
        d.close()

        elapsed = time.time() - st

        # update locations to signal we've run tensorflow on this file
        c.execute('UPDATE locations SET objectDetectionRanAt=?,objectDetectionRunSeconds=? WHERE sha1=?', (datetime.datetime.now().timestamp(), elapsed, fileSha1))

        db.commit()


db.close()
