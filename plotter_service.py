from contextlib import redirect_stderr, redirect_stdout
import io


def plot(ad, filepath, layer=0, model_number=4):
    """Plot an SVG file, optionally restricted to a single numbered layer."""
    ad.options.model = model_number
    ad.plot_setup(filepath)
    ad.options.mode = "plot"
    ad.options.auto_rotate = False
    ad.options.reordering = 0
    ad.options.check_limits = True
    ad.options.clip_to_page = True

    if layer > 0:
        ad.options.mode = "layers"
        ad.options.layer = layer

    ad.plot_run()

    ad.options.mode = "manual"
    ad.options.manual_cmd = "disable_xy"
    ad.plot_run()


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
            ad.options.model = model_number
            ad.plot_setup(filepath)
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
        ad.options.model = model_number
        ad.plot_setup()
        ad.options.preview = False
        ad.options.mode = "toggle"
        ad.plot_run()
    finally:
        if previous_mode is not None:
            ad.options.mode = previous_mode
        if previous_model is not None:
            ad.options.model = previous_model
        ad.options.preview = previous_preview