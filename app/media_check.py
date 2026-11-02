"""Read real video streams and endpoint frames before a session becomes deliverable."""
from obs_control import ObsUnavailable

def inspect_video(path):
    import av
    try:
        with av.open(str(path), mode='r', timeout=10) as container:
            if not container.streams.video:
                raise ValueError('No video stream')
            stream = container.streams.video[0]
            length = float(container.duration / av.time_base) if container.duration is not None else (
                float(stream.duration * stream.time_base) if stream.duration is not None else 0)
            if length < 1:
                raise ValueError('Video duration is missing or shorter than one second')
            first = next(container.decode(stream), None)
            if first is None or first.width <= 0 or first.height <= 0:
                raise ValueError('The first video frame cannot be decoded')
            container.seek(int(max(0, length - 2) * av.time_base), backward=True)
            if next(container.decode(stream), None) is None:
                raise ValueError('The final video section cannot be decoded')
            return {'duration': length, 'width': first.width, 'height': first.height, 'codec': stream.codec_context.name}
    except Exception as exc:
        raise ObsUnavailable('Video verification failed: ' + str(exc)) from exc

def verify_pair(paths, expected_duration=0):
    metadata = {key: inspect_video(path) for key, path in paths.items()}
    durations = [m['duration'] for m in metadata.values()]
    if max(durations) - min(durations) > 10:
        raise ObsUnavailable('Game and Camera durations differ by more than 10 seconds. Sending is blocked.')
    if expected_duration > 10 and any(abs(duration - expected_duration) > max(10, expected_duration * .03) for duration in durations):
        raise ObsUnavailable('A video does not cover the recorded session duration. Sending is blocked.')
    return metadata
