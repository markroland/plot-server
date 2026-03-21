import re


def parse_duration_to_seconds(duration_text):
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