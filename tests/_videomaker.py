import av, numpy as np, fractions
def make_video(path, colors_per_sec, fps=5, size=(64,64), gop=15):
    """colors_per_sec: list of (r,g,b) one per second."""
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=fps)
        s.width, s.height, s.pix_fmt, s.gop_size = size[0], size[1], "yuv420p", gop
        for col in colors_per_sec:
            for _ in range(fps):
                arr = np.zeros((size[1], size[0], 3), np.uint8); arr[:] = col
                f = av.VideoFrame.from_ndarray(arr, format="rgb24")
                for p in s.encode(f): c.mux(p)
        for p in s.encode(None): c.mux(p)
