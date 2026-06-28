#!/usr/bin/env python3

import html
import pathlib

from .results import atomic_write_text


COLORS = ["#2457C5", "#D9544D", "#2A9D67", "#8B5FBF", "#D28B19", "#4B778D"]


def dot_plot(path, title, rows, value_key, ylabel):
    rows = [row for row in rows if row.get(value_key) not in (None, "")]
    width, height = 900, 440
    left, right, top, bottom = 90, 30, 55, 95
    values = [float(row[value_key]) for row in rows]
    maximum = max(values) if values else 1.0
    maximum = maximum if maximum > 0 else 1.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    count = max(1, len(rows))
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">{html.escape(title)}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top+plot_height}" x2="{left+plot_width}" y2="{top+plot_height}" stroke="#333"/>',
        f'<text x="18" y="{top+plot_height/2}" transform="rotate(-90 18 {top+plot_height/2})" text-anchor="middle" font-family="sans-serif" font-size="13">{html.escape(ylabel)}</text>',
    ]
    for tick in range(6):
        value = maximum * tick / 5
        y = top + plot_height - plot_height * tick / 5
        elements.append(f'<line x1="{left-5}" y1="{y:.2f}" x2="{left+plot_width}" y2="{y:.2f}" stroke="#ddd"/>')
        elements.append(f'<text x="{left-10}" y="{y+4:.2f}" text-anchor="end" font-family="monospace" font-size="11">{value:.3g}</text>')
    for index, row in enumerate(rows):
        x = left + plot_width * (index + 0.5) / count
        y = top + plot_height - plot_height * float(row[value_key]) / maximum
        label = f"{row['condition_id']} t{row['trial_number']}"
        elements.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{COLORS[index % len(COLORS)]}"/>')
        elements.append(f'<text x="{x:.2f}" y="{top+plot_height+16}" transform="rotate(35 {x:.2f} {top+plot_height+16})" font-family="sans-serif" font-size="10">{html.escape(label)}</text>')
    if not rows:
        elements.append(f'<text x="{width/2}" y="{height/2}" text-anchor="middle" font-family="sans-serif">No data</text>')
    elements.append('</svg>')
    atomic_write_text(pathlib.Path(path), "\n".join(elements) + "\n")


def line_plot(path, title, rows, x_key, y_key, xlabel, ylabel):
    rows = [
        row for row in rows
        if row.get(x_key) not in (None, "") and row.get(y_key) not in (None, "")
    ]
    width, height = 900, 440
    left, right, top, bottom = 90, 30, 55, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="18">{html.escape(title)}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top+plot_height}" x2="{left+plot_width}" y2="{top+plot_height}" stroke="#333"/>',
        f'<text x="{width/2}" y="{height-16}" text-anchor="middle" font-family="sans-serif" font-size="13">{html.escape(xlabel)}</text>',
        f'<text x="18" y="{top+plot_height/2}" transform="rotate(-90 18 {top+plot_height/2})" text-anchor="middle" font-family="sans-serif" font-size="13">{html.escape(ylabel)}</text>',
    ]
    if rows:
        xs = [float(row[x_key]) for row in rows]
        ys = [float(row[y_key]) for row in rows]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        if xmin == xmax:
            xmax = xmin + 1.0
        if ymin == ymax:
            ymax = ymin + 1.0
        points = []
        for row in sorted(rows, key=lambda item: float(item[x_key])):
            x = left + (float(row[x_key]) - xmin) * plot_width / (xmax - xmin)
            y = top + plot_height - (float(row[y_key]) - ymin) * plot_height / (ymax - ymin)
            points.append(f"{x:.2f},{y:.2f}")
            elements.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#2457C5"/>')
        elements.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#2457C5" stroke-width="2"/>')
        elements.append(f'<text x="{left}" y="{top+plot_height+18}" font-family="monospace" font-size="10">{xmin:.4g}</text>')
        elements.append(f'<text x="{left+plot_width}" y="{top+plot_height+18}" text-anchor="end" font-family="monospace" font-size="10">{xmax:.4g}</text>')
        elements.append(f'<text x="{left-8}" y="{top+4}" text-anchor="end" font-family="monospace" font-size="10">{ymax:.4g}</text>')
        elements.append(f'<text x="{left-8}" y="{top+plot_height}" text-anchor="end" font-family="monospace" font-size="10">{ymin:.4g}</text>')
    else:
        elements.append(f'<text x="{width/2}" y="{height/2}" text-anchor="middle" font-family="sans-serif">No data</text>')
    elements.append('</svg>')
    atomic_write_text(pathlib.Path(path), "\n".join(elements) + "\n")
