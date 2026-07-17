import re


def parse_duration_to_seconds(duration_text):
    """Convert a preview duration string into a total number of seconds."""
    parts = [int(part) for part in duration_text.split(':')]

    if len(parts) == 3:
        hours, minutes, seconds = parts
        return (hours * 3600) + (minutes * 60) + seconds

    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes * 60) + seconds

    if len(parts) == 1:
        return parts[0]

    raise ValueError(f'Unsupported duration format: {duration_text}')


def parse_preview_output(preview_output):
    """Extract timing and distance metrics from AxiDraw preview console output."""
    duration_match = re.search(r'Estimated print time:\s*([0-9:]+)', preview_output)
    path_match = re.search(r'Length of path to draw:\s*([0-9]+(?:\.[0-9]+)?)\s*m', preview_output)
    travel_match = re.search(r'Pen-up travel distance:\s*([0-9]+(?:\.[0-9]+)?)\s*m', preview_output)

    if not duration_match or not path_match or not travel_match:
        raise ValueError('Could not parse preview output')

    return {
        'plot_duration': parse_duration_to_seconds(duration_match.group(1)),
        'plot_path': float(path_match.group(1)),
        'plot_travel': float(travel_match.group(1)),
    }


def parse_plot_output(plot_output):
    """Extract elapsed timing and distance/lift metrics from plot console output."""
    elapsed_match = re.search(r'Elapsed time:\s*([0-9:]+)', plot_output)
    path_match = re.search(r'Length of path drawn:\s*([0-9]+(?:\.[0-9]+)?)\s*m', plot_output)
    distance_match = re.search(r'Total distance moved:\s*([0-9]+(?:\.[0-9]+)?)\s*m', plot_output)

    lift_patterns = [
        r'(?im)^.*number\s+of\s+pen\s+lifts?\s*[:=]?\s*([0-9][0-9,]*)\b',
        r'(?im)^.*pen\s*-?\s*lifts?\s*[:=]?\s*([0-9][0-9,]*)\b',
        r'(?im)^.*pen\s*lift\s*count\s*[:=]?\s*([0-9][0-9,]*)\b',
        r'(?im)^.*pen\s*-?\s*down\s*events?\s*[:=]?\s*([0-9][0-9,]*)\b',
        r'(?im)^.*lifts?\s*[:=]?\s*([0-9][0-9,]*)\b',
    ]
    lifts_match = None
    for pattern in lift_patterns:
        lifts_match = re.search(pattern, plot_output, re.IGNORECASE)
        if lifts_match:
            break

    if not elapsed_match:
        raise ValueError('Could not parse elapsed plot duration')

    lifts_value = 0
    if lifts_match:
        lifts_value = int(lifts_match.group(1).replace(',', ''))

    return {
        'plot_duration': parse_duration_to_seconds(elapsed_match.group(1)),
        'plot_path': float(path_match.group(1)) if path_match else 0.0,
        'plot_travel': float(distance_match.group(1)) if distance_match else 0.0,
        'lifts': lifts_value,
    }