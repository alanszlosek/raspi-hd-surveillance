import glob
import json
import os

header = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Browse Videos</title>

<style>
html {
    -webkit-text-size-adjust: 100%;
}
html,body,button,input,textarea,div,em,a {
    box-sizing: border-box;
    color: #636363;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 1.0em;
    margin: 0;
    padding: 0;
}
/*
* {
    font-size: 1.05em;
}
*/

body, button, input, textarea {
}
button {
    background-color: #3372b3;
    border: 0;
    border-radius: .15em;
    color: #fff;
    cursor: pointer;
    display: inline-block;
    height: auto;
    line-height: 1.7em;
    padding: .3em 1.1em;
    text-align: center;
    vertical-align: middle;
    width: auto;
}
input, textarea {
    background-color: #f5f5f5; /*#fbfbfb;*/
    color: #636363;
    border-top: 0px;
    border-right: 0px;
    border-bottom: 1px solid #ccc;
    border-left: 0px;
    padding: 0.2em;
}
input {
    /* This is to gently nudge the input narrower in the tag list above addNote */
    min-width: 60px;
}
button.notice, #nav > div.notice {
    background-color: #ff7c39;
}
.success, button.save {
    background-color: #2084e9 !important;
    color: #fff;
}
.disabled {
    background-color: #ccc !important;
}
.smaller {
    padding: .3em;
}
:focus {
    outline: none;
}

div.search {
    background-color: #fbfbfb;
    color: #636363;
}
div.search input {
    border: 0;
    width: 100%;
}

.flex {
    display: flex;
    flex-direction: row;
    flex-wrap: wrap;
    margin: 0;
    width: auto;
}
.flex > * {
    flex: 1;
    padding-left:0;
}
.flex.one > * {
    flex: 1 1 100%;
}
.flex.three > div {
    flex: 1 1 33.3333%;
}
.flex.four > div {
    flex: 1 1 25%;
}
.flex.five > * {
    flex: 1 1 20%;
}
.flex.six > div {
    flex: 1 1 16.6666%;
}
.flex.seven > div {
    flex: 1 1 14.2857%;
}
.button.smaller {
    font-size: smaller; /*.75em;*/
}

.noscroll {
    overflow: hidden;
}
.overlay {
    background-color: #fff;
    bottom: 0;
    display: none;
    left: 0;
    overflow-y: scroll;
    position: fixed;
    right: 0;
    top: 0;
    z-index: 2;
}
.container {
    margin-left: auto;
    margin-right: auto;
    max-width: 1000px;
}

.right {
    float: right;
}

.hidden {
    display: none;
}

.push {
    margin-bottom: 10px;
}


.main {

}

.main video {
    width: 100%;
}
.main > div {
    border: 1px solid #fff;
    padding: 2px;
}
.main > div.selected {
    background-color: beige;
}

</style>
</head>
<body id="body">

<div id="main" class="main flex four">
"""

footer = """
</div>
<script>
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

var tag = function(tagName, attributes) {
    var args = Array.prototype.slice.call(arguments);
    tagName = args.shift();
    attributes = args.shift();

    var element = document.createElement(tagName);
    for (var i in attributes) {
        element.setAttribute(i, attributes[i]);
    }
    // Convert text to text node
    for (var i = 0; i < args.length; i ++) {
        var node = args[i];
        if (node == null) {
            continue;
        } else if (node instanceof Node) {
        } else {
            node = document.createTextNode(args[i]);
        }
        element.appendChild(node);
    }

    return element;
};

var handles = {
    body: document.body,
    videos: document.getElementById('main')
};

/*
KEYBOARD INPUT THOUGHTS

* would like left right arrow keys to work as seek
* page up down as faster seek

Questions

* What should advance to next/previous video? up and down arrow keys?

*/
var handlers = {
    keyup: {
        body: function(e) {
            console.log(e)
            // left arrow
            switch (e.keyCode) {
                // space
                case 32:
                    if (currentlyPlaying) {
                        if (currentlyPlaying.paused) {
                            currentlyPlaying.play();
                        } else {
                            currentlyPlaying.pause();
                        }
                    }
                    break;
                // left arrow
                case 37:
                    prev();
                    break;
                // right arrow
                case 39:
                    next();
                    break;
            }
        }
    }
};
// Attach the above handlers
for (var event in handlers) {
    var ids = handlers[event];
    for (var id in ids) {
        document.getElementById(id).addEventListener(event, ids[id]);
    }
}



// CONVERT THE VIDEOS JSON INTO DIV AND VIDEO TAGS
var videos = %s;
var tags = [];
var queue = [];
for (var i in videos) {
    var video = videos[i];
    var videoTag = tag('video', {'preload': 'metadata', 'controls': 'controls', 'data-src': video});
    tags.push(
        tag('div',
            {},
            videoTag,
            video,
            '#tags'
        )
    );
    queue.push(videoTag);
}
drawChildren(handles.videos, tags);
// Now go through the queue, and gradually set the src of each video tag
// this is so we don't overload the browser by asking it to load metadata for 10+ videos at once
var loadMetaData = function() {
    if (queue.length == 0) {
        console.log('done loading video metadata');
        return;
    }
    var videoTag = queue.shift();
    videoTag.addEventListener('loadedmetadata', function() {
        setTimeout(loadMetaData, 10);
    });
    videoTag.src = videoTag.getAttribute('data-src');
};
loadMetaData();


var currentlyPlaying;

var prev = function() {
    if (currentlyPlaying) {
        currentlyPlaying.pause();

        // TODO: handle prev being null
        currentlyPlaying = currentlyPlaying.parentNode.previousElementSibling.childNodes[0];
        if (currentlyPlaying) {
            // assuming it's loaded
            currentlyPlaying.play();
        }

    }
};

var next = function() {
    if (currentlyPlaying) {
        currentlyPlaying.pause();

        // TODO: handle nextElementSibling being null
        currentlyPlaying = currentlyPlaying.parentNode.nextElementSibling.childNodes[0];
        if (currentlyPlaying) {
            // assuming it's loaded
            currentlyPlaying.play();
        }
    } else {
        // nothing current, start at the first video
        var tags = handles.videos.querySelectorAll('div');
        if (tags.length > 0) {
            tags[0].childNodes[0].play();
        }
    }
};

/*
Now need something here that sets video source, waits for "loadedmetadata" event, then sets the next source
*/
var tags = document.getElementsByTagName('video');
for (var i = 0; i < tags.length; i++) {
    var el = tags[i];
    /*
    Relevant events
    canplay
    complete
    ended
    loadeddata
    loadedmetadata
    pause
    play
    playing
    */
    el.addEventListener('ended', function(e) {
        console.log(e);
        // go to next
        next();
    });
    el.addEventListener('loadedmetadata', function(e) {
        console.log(e);
    });
    el.addEventListener('playing', function(e) {
        console.log(e);
        // remove all other borders
        for (let vid of document.getElementsByTagName('video')) {
            vid.parentNode.classList.remove('selected');
        }

        e.target.parentNode.classList.add('selected');
        currentlyPlaying = e.target;
    });
    
}

</script>
</body>
</html>
"""

f = open('index.html', mode='wt')
f.write(header)
files = glob.glob('*.mp4')
files.sort(reverse=True)
files = files[0:4]
files = list(map(os.path.basename, files))
f.write(footer % json.dumps(files))
f.close()
