"""Portable, offline evidence viewer for Direct sessions."""

from html import escape
import json
from pathlib import Path
from urllib.parse import quote


def write_timeline(state: dict) -> Path:
    root = Path(state["output_dir"]).resolve()

    def relative(path: str) -> str:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(root):
            return ""
        return quote(resolved.relative_to(root).as_posix())

    videos = []
    for recording in state.get("recordings", []):
        manifest_path = Path(recording["output_dir"]) / "recording.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for segment in manifest.get("segments", []):
                src = relative(str(manifest_path.parent / segment["file"]))
                if src:
                    videos.append(
                        f'<video controls preload="metadata" src="{src}" '
                        f'data-start="{int(segment["offset_ms"])}" '
                        f'data-end="{int(segment["offset_ms"]) + int(segment["duration_ms"])}">'
                        "</video>"
                    )
        elif recording.get("video_path"):
            src = relative(recording["video_path"])
            if src:
                videos.append(f'<video controls preload="metadata" src="{src}"></video>')

    events = []
    for kind, key in (("Action completed", "actions"), ("Observation", "observations"),
                      ("Checkpoint", "checkpoints")):
        for event in state[key]:
            offset = int(event.get("offset_ms", 0))
            title = event.get("name") or event.get("action") or str(event["index"])
            detail = event.get("details") or event.get("result", {}).get("message", "")
            status = event.get("outcome", "")
            screenshot = (event.get("observation") or {}).get("screenshot_path")
            src = relative(screenshot) if screenshot else ""
            evidence = f'<a href="{src}"><img loading="lazy" src="{src}"></a>' if src else ""
            events.append((offset,
                f'<article><button data-time="{offset}">T+{offset / 1000:.3f}s</button> '
                f'{escape(kind)}: {escape(title)} <b>{escape(status)}</b>'
                f'<p>{escape(detail)}</p>{evidence}</article>'))
    body = "\n".join(event for _, event in sorted(events, key=lambda item: item[0]))
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Direct evidence timeline</title><style>'
        'body{font:16px system-ui;max-width:1100px;margin:24px auto;padding:0 16px}'
        'video{max-width:360px;max-height:60vh}img{max-width:180px;max-height:320px}'
        'article{border-top:1px solid #bbb;padding:16px 0}button{cursor:pointer}'
        '</style></head><body>'
        f'<h1>{escape(state["name"])}</h1><p>Status: {escape(state["status"])}</p>'
        '<p>Times mark action completion or evidence capture, not exact touch onset. '
        'Recording segment offsets use the recorder first-frame estimate; gaps are preserved.</p>'
        + "\n".join(videos) + body +
        """<script>
document.querySelectorAll('button[data-time]').forEach(button => {
  button.onclick = () => {
    const t = Number(button.dataset.time);
    document.querySelectorAll('video[data-start]').forEach(video => {
      if (t >= Number(video.dataset.start) && t < Number(video.dataset.end)) {
        video.currentTime = (t - Number(video.dataset.start)) / 1000;
        video.scrollIntoView({block: 'center'});
      }
    });
  };
});
</script></body></html>"""
    )
    path = root / "timeline.html"
    path.write_text(html, encoding="utf-8")
    return path
