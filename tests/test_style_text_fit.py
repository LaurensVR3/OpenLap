"""
Tests for overlay_utils.fit_text_to_width() — the shrink-to-fit helper that
replaces the old box-dimension-only font sizing (which let long value strings
overflow gauge boundaries; see gauge_numeric.py etc.).
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pytest

from overlay_utils import fit_text_to_width


def _make_text(text, fontsize, fig_w_px=200, fig_h_px=100):
    dpi = 100
    fig = plt.figure(figsize=(fig_w_px / dpi, fig_h_px / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    text_obj = ax.text(0.5, 0.5, text, ha='center', va='center', fontsize=fontsize)
    return fig, text_obj


def test_shrinks_when_too_wide():
    fig, text_obj = _make_text('123,456,789', fontsize=34)
    renderer = fig.canvas.get_renderer()
    width_before = text_obj.get_window_extent(renderer=renderer).width

    budget = 60
    new_fs = fit_text_to_width(fig, text_obj, budget)

    assert new_fs < 34
    assert text_obj.get_fontsize() == pytest.approx(new_fs)
    renderer2 = fig.canvas.get_renderer()
    width_after = text_obj.get_window_extent(renderer=renderer2).width
    assert width_after <= budget * 1.05  # small tolerance for the single-shot linear correction
    plt.close(fig)
    assert width_before > budget  # sanity: it really was too wide to start with


def test_noop_when_it_already_fits():
    fig, text_obj = _make_text('5', fontsize=12)
    new_fs = fit_text_to_width(fig, text_obj, max_width_px=500)
    assert new_fs == 12
    assert text_obj.get_fontsize() == 12
    plt.close(fig)


def test_clamped_to_min_fontsize():
    fig, text_obj = _make_text('a very long string that will not fit', fontsize=40)
    new_fs = fit_text_to_width(fig, text_obj, max_width_px=2, min_fontsize=6)
    assert new_fs == 6
    assert text_obj.get_fontsize() == 6
    plt.close(fig)


def test_never_grows_past_candidate_when_candidate_below_min_fontsize():
    """Regression: if the starting fontsize is already below min_fontsize (can
    happen for tightly-packed table rows), shrinking further must not clamp
    the result *up* to min_fontsize — that would grow the text instead of
    shrinking it, defeating the whole point of this helper."""
    fig, text_obj = _make_text('123456789', fontsize=5)
    new_fs = fit_text_to_width(fig, text_obj, max_width_px=2, min_fontsize=6)
    assert new_fs <= 5
    plt.close(fig)


@pytest.mark.parametrize('style_module,style_name,data', [
    ('gauge_numeric', 'Numeric', {'value': 123456.789, 'label': 'RPM', 'unit': 'rpm', 'channel': 'rpm'}),
    ('gauge_dial', 'Dial', {'value': -123456.789, 'label': 'Speed', 'unit': 'km/h', 'channel': 'speed',
                             'min_val': 0, 'max_val': 300}),
    ('gauge_delta', 'Delta', {'value': -12345.6789, 'label': 'Delta'}),
    ('gauge_lean', 'Lean', {'value': -89.999, 'label': 'Lean', 'unit': '°'}),
])
def test_extreme_values_render_without_error_at_small_size(style_module, style_name, data):
    """Regression guard: extreme/long value strings must not raise or distort
    output shape when rendered at a small gauge size (where overflow used to
    be most visible)."""
    import style_registry
    img = style_registry.render_style('gauge', style_name, data, 80, 100)
    assert img.shape == (100, 80, 4)
