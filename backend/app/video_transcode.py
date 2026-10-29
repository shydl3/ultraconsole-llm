"""Isolated, hardware-only preview conversion worker (no model imports)."""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path


def transcode_gpu(source: Path, target: Path, progress) -> None:
    # PyAV bundles FFmpeg; no separate ffmpeg executable is required.
    import av
    with av.open(str(source)) as incoming:
        if not incoming.streams.video:
            raise ValueError("No video stream")
        video = incoming.streams.video[0]
        # CUVID is hardware-only. Unlike HWAccel in PyAV 16, it does not use
        # the get_format callback that can segfault on unsupported input.
        codec = video.codec_context.name
        supported = {"h264", "hevc", "av1", "vp8", "vp9", "mjpeg", "mpeg1video", "mpeg2video", "mpeg4", "vc1"}
        if codec not in supported:
            raise ValueError(f"Selected video stream has no NVIDIA decoder: {codec}")
        decoder_name = {"mpeg1video": "mpeg1", "mpeg2video": "mpeg2"}.get(codec, codec) + "_cuvid"
        decoder = av.CodecContext.create(decoder_name, "r")
        decoder.extradata = video.codec_context.extradata
        decoder.options = {"gpu": "0"}
        width, height = video.codec_context.width, video.codec_context.height
        if width <= 0 or height <= 0 or width * height > 33_177_600:
            raise ValueError("Invalid video dimensions or video exceeds 8K")
        duration = float(video.duration * video.time_base) if video.duration else (incoming.duration or 0) / 1_000_000
        rate = video.average_rate or Fraction(25)
        time_base = Fraction(1, 90000)
        with av.open(str(target), "w", format="mp4", options={"movflags": "+faststart"}) as output:
            stream = output.add_stream("h264_nvenc", rate=rate)
            stream.width, stream.height = width + width % 2, height + height % 2
            stream.pix_fmt = "nv12"
            stream.time_base = time_base
            stream.codec_context.time_base = time_base
            stream.options = {
                "gpu": "0", "preset": "p4", "rc": "vbr", "cq": "20", "b": "0",
                "g": str(max(1, round(float(rate)))), "bf": "0",
            }
            start = None
            last_pts = -1
            count = 0
            for frame in (frame for packet in incoming.demux(video) for frame in decoder.decode(packet)):
                # Normalize surveillance timestamps; keep variable frame timing for analysis.
                if frame.pts is not None:
                    stamp = frame.pts * frame.time_base
                    if start is None:
                        start = stamp
                    pts = round((stamp - start) / time_base)
                else:
                    pts = last_pts + max(1, round(1 / rate / time_base))
                # CUVID transfers NVDEC output to host memory. Reformat is a
                # no-op for normal even-sized NV12 video; NVENC still encodes on
                # GPU. Pixel conversion (e.g. 10-bit -> 8-bit) is not decoding.
                frame = frame.reformat(width=stream.width, height=stream.height, format="nv12")
                frame.pts = max(last_pts + 1, pts)
                frame.time_base = time_base
                last_pts = frame.pts
                for packet in stream.encode(frame):
                    output.mux(packet)
                count += 1
                if count % 25 == 0:
                    progress(min(99, round(float(last_pts * time_base) / duration * 100)) if duration > 0 else 0)
            if not count:
                raise ValueError("No decodable video frames")
            for packet in stream.encode():
                output.mux(packet)


if __name__ == "__main__":
    try:
        transcode_gpu(Path(sys.argv[1]), Path(sys.argv[2]), lambda value: print(value, flush=True))
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
