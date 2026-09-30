from contextlib import redirect_stderr, redirect_stdout
import io

from axidrawinternal import serial_utils
from plotink import ebb_motion


def plot(ad, filepath, layer=0, model_number=4):
    """Plot an SVG file, optionally restricted to a single numbered layer."""
    output_buffer = io.StringIO()
    lifts_value = 0
    previous_mode = getattr(ad.options, 'mode', None)
    previous_layer = getattr(ad.options, 'layer', None)
    previous_model = getattr(ad.options, 'model', None)
    previous_report_time = getattr(ad.options, 'report_time', False)
    previous_report_lifts = getattr(ad.options, 'report_lifts', False)

    try:
        with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
            ad.plot_setup(filepath)
            # plot_setup() resets options to defaults (model 1), so set the model afterward
            ad.options.model = model_number
            ad.options.mode = "plot"
            ad.options.auto_rotate = False
            ad.options.reordering = 4
            ad.options.check_limits = True
            ad.options.clip_to_page = True
            ad.options.report_time = True
            ad.options.report_lifts = True

            if layer > 0:
                ad.options.mode = "layers"
                ad.options.layer = layer

            ad.plot_run()
            lifts_value = int(getattr(ad, 'pen_lifts', 0) or 0)

            ad.options.mode = "manual"
            ad.options.manual_cmd = "disable_xy"
            ad.plot_run()
    finally:
        ad.options.report_time = previous_report_time
        ad.options.report_lifts = previous_report_lifts
        if previous_mode is not None:
            ad.options.mode = previous_mode
        if previous_layer is not None:
            ad.options.layer = previous_layer
        if previous_model is not None:
            ad.options.model = previous_model

    output = output_buffer.getvalue().strip()
    if not output:
        output = 'Plot completed with no output.'

    if 'pen lift' not in output.lower():
        output = f"{output}\nNumber of pen lifts: {lifts_value}" if output else f"Number of pen lifts: {lifts_value}"

    return output


def preview_plot(ad, filepath, layer=0, model_number=4):
    """Run a preview pass and return the captured AxiDraw output text."""
    output_buffer = io.StringIO()
    previous_preview = getattr(ad.options, 'preview', False)
    previous_report_time = getattr(ad.options, 'report_time', False)
    previous_mode = getattr(ad.options, 'mode', None)
    previous_layer = getattr(ad.options, 'layer', None)
    previous_model = getattr(ad.options, 'model', None)

    try:
        with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
            ad.plot_setup(filepath)
            # plot_setup() resets options to defaults (model 1), so set the model afterward
            ad.options.model = model_number
            if layer > 0:
                ad.options.mode = "layers"
                ad.options.layer = layer
            ad.options.preview = True
            ad.options.report_time = True
            ad.plot_run()
    finally:
        ad.options.preview = previous_preview
        ad.options.report_time = previous_report_time
        if previous_mode is not None:
            ad.options.mode = previous_mode
        if previous_layer is not None:
            ad.options.layer = previous_layer
        if previous_model is not None:
            ad.options.model = previous_model

    output = output_buffer.getvalue().strip()
    if not output:
        output = 'Preview completed with no output.'

    return output


def toggle_servo(ad, model_number=4):
    """Toggle the AxiDraw pen servo using the utility toggle mode."""
    previous_mode = getattr(ad.options, 'mode', None)
    previous_preview = getattr(ad.options, 'preview', False)
    previous_model = getattr(ad.options, 'model', None)

    try:
        ad.plot_setup()
        ad.options.model = model_number
        ad.options.preview = False
        ad.options.mode = "toggle"
        ad.plot_run()
    finally:
        if previous_mode is not None:
            ad.options.mode = previous_mode
        if previous_model is not None:
            ad.options.model = previous_model
        ad.options.preview = previous_preview


def disable_steppers(ad, model_number=4):
    """Disable the AxiDraw's XY stepper motors, freeing the carriage to be moved by hand."""
    previous_mode = getattr(ad.options, 'mode', None)
    previous_manual_cmd = getattr(ad.options, 'manual_cmd', None)
    previous_preview = getattr(ad.options, 'preview', False)
    previous_model = getattr(ad.options, 'model', None)

    try:
        ad.plot_setup()
        ad.options.model = model_number
        ad.options.preview = False
        ad.options.mode = "manual"
        ad.options.manual_cmd = "disable_xy"
        ad.plot_run()
    finally:
        if previous_mode is not None:
            ad.options.mode = previous_mode
        if previous_manual_cmd is not None:
            ad.options.manual_cmd = previous_manual_cmd
        if previous_model is not None:
            ad.options.model = previous_model
        ad.options.preview = previous_preview


CARRIAGE_POSITIONS = ("home", "top-right", "bottom-right", "bottom-left")


def _query_carriage_steps(ad):
    """Read the AxiDraw's raw motor step counts, waiting for any pending motion to finish first."""
    serial_utils.exhaust_queue(ad)
    return ebb_motion.query_steps(ad.plot_status.port, False)


def _steps_to_inches(a_pos, b_pos, a_ref, b_ref, ad):
    """Convert a step-count reading into an (x, y) inch offset from a reference step reading.

    Mirrors the math AxiDraw's own "walk_home" command uses to convert step counts to distance.
    """
    delta_a = a_pos - a_ref
    delta_b = b_pos - b_ref
    res_factor = ad.params.native_res_factor
    offset_x = (delta_a + delta_b) / (4 * res_factor)
    offset_y = (delta_a - delta_b) / (4 * res_factor)
    if ad.options.resolution == 2:
        offset_x *= 2
        offset_y *= 2
    return offset_x, offset_y


def move_carriage(ad, position, model_number=4, reference_steps=None):
    """Move the AxiDraw carriage (pen up) to a named reference corner of the travel area.

    Returns ((x, y) coordinates moved to, in inches; updated reference_steps to use for
    the next call). reference_steps is the step reading last confirmed to correspond to
    home -- pass back the value returned from here so later moves account for everything
    that has happened since. Without one yet (e.g. right after a software-only "Reset to
    Home", which doesn't touch hardware), this move's own first step reading is trusted
    as home and used as the new baseline.
    """
    if position not in CARRIAGE_POSITIONS:
        raise ValueError(f"Unknown carriage position: {position}")

    ad.interactive()
    ad.options.model = model_number
    # These are long, full-travel rapid moves (unlike the short pen-up jumps within a
    # normal plot), so favor a gentler speed/acceleration -- open-loop steppers have no
    # position feedback, and a skipped step from too-aggressive motion is invisible to
    # software and will silently show up as drift on the next command.
    ad.options.speed_penup = min(ad.options.speed_penup, 50)
    ad.options.accel = min(ad.options.accel, 50)

    if not ad.connect():
        raise RuntimeError("Failed to connect to AxiDraw")

    try:
        x_min, y_min = ad.bounds[0]
        x_max, y_max = ad.bounds[1]

        # Each connection starts a fresh software position tracker at (0, 0), which is
        # only correct if the carriage happens to be at home already. The EBB board's
        # own step counters persist across reconnects (as long as it stays powered), so
        # read those to recover the carriage's true current position before moving --
        # otherwise a move is planned as a delta from a stale/incorrect starting point.
        a_pos, b_pos = _query_carriage_steps(ad)

        updated_reference_steps = reference_steps

        if a_pos is not None and b_pos is not None:
            if reference_steps is None:
                # No calibration yet -- trust that wherever the carriage is right now is
                # home (this is how a software-only Reset to Home takes effect), and
                # establish that as the baseline for this and future moves.
                reference_steps = (a_pos, b_pos)
                updated_reference_steps = reference_steps

            a_ref, b_ref = reference_steps
            offset_x, offset_y = _steps_to_inches(a_pos, b_pos, a_ref, b_ref, ad)

            ad.pen.phys.xpos = x_min + offset_x
            ad.pen.phys.ypos = y_min + offset_y
            ad.pen.turtle.xpos = ad.pen.phys.xpos
            ad.pen.turtle.ypos = ad.pen.phys.ypos

        targets = {
            "home": (x_min, y_min),
            "top-right": (x_max, y_min),
            "bottom-right": (x_max, y_max),
            "bottom-left": (x_min, y_max),
        }

        target_x, target_y = targets[position]
        ad.moveto(target_x, target_y)

        if position == "home":
            # Refresh calibration from this known-good position so small rounding errors
            # don't accumulate across many moves.
            new_a_pos, new_b_pos = _query_carriage_steps(ad)
            if new_a_pos is not None and new_b_pos is not None:
                updated_reference_steps = (new_a_pos, new_b_pos)

        return (target_x, target_y), updated_reference_steps
    finally:
        ad.disconnect()