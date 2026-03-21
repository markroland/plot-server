from contextlib import redirect_stderr, redirect_stdout
import io


def plot(ad, filepath, layer=0, model_number=4):
    ad.plot_setup(filepath)
    ad.options.mode = "plot"
    ad.options.model = model_number
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


def preview_plot(ad, filepath):
    output_buffer = io.StringIO()
    previous_preview = getattr(ad.options, 'preview', False)
    previous_report_time = getattr(ad.options, 'report_time', False)

    try:
        with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
            ad.plot_setup(filepath)
            ad.options.preview = True
            ad.options.report_time = True
            ad.plot_run()
    finally:
        ad.options.preview = previous_preview
        ad.options.report_time = previous_report_time

    output = output_buffer.getvalue().strip()
    if not output:
        output = 'Preview completed with no output.'

    return output