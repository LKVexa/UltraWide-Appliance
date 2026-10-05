"""Render actual BRWWORD bytes as an exact bit matrix (optional Pillow dependency).

Run from a source checkout with Pillow installed in the development environment:
    python scripts/render_word_image.py --replace
The runtime itself does not depend on Pillow. Existing differing outputs are
refused unless --replace is explicitly provided. No downloaded assets are used.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ultrawide import brim, brim_wide

WIDTH, HEIGHT = 1600, 1920
MATRIX_X, MATRIX_Y = 64, 350
MATRIX_SIDE = 1024
ZERO = (17, 28, 40)
ONE = (111, 235, 211)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def store(path, data, replace):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link output')
    if path.exists():
        if path.read_bytes() == data:
            return
        if not replace:
            raise FileExistsError('Output differs; use --replace to regenerate it')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb' if replace else 'xb') as stream:
        stream.write(data)


def bit_image(data, columns):
    """Pixel i is the actual bit (byte[i//8] >> (i%8)) & 1, no resampling."""
    from PIL import Image
    if len(data) * 8 % columns:
        raise ValueError('Bits must fill complete rows')
    pixels = bytes(bit for byte in data for bit in ((byte >> offset) & 1 for offset in range(8)))
    picture = Image.frombytes('P', (columns, len(pixels) // columns), pixels)
    picture.putpalette(list(ZERO + ONE) + [0] * (768 - 6))
    return picture.convert('RGB')


def render(blob):
    from PIL import Image, ImageDraw, ImageFont
    chain = brim_wide.verify_chain(blob)
    if len(chain.programs) != 1 or len(blob) != 131072:
        raise ValueError('The figure must show one independently packed complete word')
    item = chain.programs[0]
    execution = brim_wide.run_chain(blob)
    image = Image.new('RGB', (WIDTH, HEIGHT), '#09111c')
    draw = ImageDraw.Draw(image)
    white, muted, faint = '#ecf3fb', '#acbdcf', '#72889e'
    accent, amber = '#6febd3', '#e7b36d'

    def text(x, y, value, size=22, color=white):
        draw.text((x, y), value, font=ImageFont.load_default(size=size), fill=color)

    text(64, 42, 'A complete BRIM program in one ultra-wide word', 42)
    text(64, 110, '1,048,576 bits = 131,072 bytes', 58, accent)
    text(64, 193, 'Actual BRWWORD/1 file data  |  16 wide registers  |  unsigned preview', 24, muted)
    draw.line((64, 253, 1536, 253), fill='#2b3c50', width=2)
    text(64, 280, 'THE ENTIRE STORED WORD', 22)
    text(64, 313, '1024 x 1024 pixels. Exactly one native-size pixel per bit.', 22, muted)

    matrix = bit_image(blob, MATRIX_SIDE)
    image.paste(matrix, (MATRIX_X, MATRIX_Y))
    draw.rectangle((MATRIX_X - 1, MATRIX_Y - 1, MATRIX_X + MATRIX_SIDE, MATRIX_Y + MATRIX_SIDE), outline='#52677e')

    x = 1132
    text(x, 279, 'BYTE LAYOUT', 22)
    offset = 0
    sections = [('Word header', brim_wide.HEADER_BYTES), ('Complete BRIM', len(item.image)),
                ('LCTL source', len(item.source)), ('BRIR', len(item.brir))]
    sections.append(('Zero padding', len(blob) - sum(size for _, size in sections)))
    for index, (label, length) in enumerate(sections):
        y = 345 + index * 94
        text(x, y, label, 24)
        text(x, y + 33, f'{length:,} bytes  |  {offset:,}..{offset + length - 1:,}', 19, muted)
        offset += length
    used = len(blob) - sections[-1][1]
    text(x, 852, 'WHY MOST PIXELS ARE DARK', 19, amber)
    for index, line in enumerate([f'Header + payload: {used:,} bytes.',
                                 f'{100 * sections[-1][1] / len(blob):.2f}% is verified zero padding.',
                                 'A short program fits completely;',
                                 'the remaining word is not invented',
                                 'or filled with decorative data.']):
        text(x, 889 + 29 * index, line, 20, muted)

    text(x, 1075, 'EXECUTION CHECK', 22)
    text(x, 1113, 'R0: highest bit set', 25, accent)
    text(x, 1150, f"Bit length: {execution['registers'][0]['bit_length']:,}", 22, muted)
    text(x, 1185, f"Verified and halted in {execution['steps']} steps.", 20, muted)
    text(x, 1239, 'Decoded BRIM operations:', 20)
    for index, line in enumerate(['MOVI R0, 1', 'SHL R0, R0, 1048575', 'HALT']):
        text(x, 1273 + 30 * index, line, 20, accent)

    draw.rectangle((64, 1402, 80, 1418), fill=ONE)
    text(91, 1397, '1', 21)
    draw.rectangle((142, 1402, 158, 1418), fill=ZERO, outline='#52677e')
    text(169, 1397, '0', 21)
    text(219, 1397, 'Top-left: bit 0 (LSB). Bottom-right: bit 1,048,575 (MSB).', 21, muted)
    text(64, 1436, 'Row-major: i = 1024*y + x. Pixel = (file[i//8] >> (i%8)) & 1.', 21, muted)
    text(64, 1470, 'Little-endian word, least-significant bit first inside each byte. This maps stored bytes, not R0.', 20, faint)

    text(64, 1522, 'PAYLOAD CLOSE-UP', 22)
    text(64, 1557, 'Word bytes 256..1279 reflowed to 256 bits/row; each bit is 4 x 4 pixels.', 21, muted)
    closeup = bit_image(blob[256:1280], 256)
    closeup = closeup.resize((1024, 128), Image.Resampling.NEAREST)
    image.paste(closeup, (64, 1596))
    draw.rectangle((63, 1595, 1088, 1724), outline='#52677e')
    text(1132, 1556, 'Exact slices, no smoothing.', 21, accent)
    for index, line in enumerate(['BRIM begins at byte 256.', 'Its source and BRIR follow.', 'Padding remains zero.']):
        text(1132, 1598 + index * 31, line, 20, muted)

    text(64, 1755, 'ACTUAL LCTL-BRIM/1 INSTRUCTION ROWS', 19, faint)
    rows = item.source.decode('utf-8').splitlines()[2:]
    for index, row in enumerate(rows):
        text(64, 1788 + index * 22, row, 16, muted)
    text(64, 1874, 'WORD SHA-256  ' + sha(blob), 17, accent)

    # Rendering fidelity is checked against all 1,048,576 original bits.
    if image.crop((MATRIX_X, MATRIX_Y, MATRIX_X + 1024, MATRIX_Y + 1024)).tobytes() != matrix.tobytes():
        raise ValueError('Figure overlay altered the native-size data matrix')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    png = buffer.getvalue()
    if len(png) >= 1024 * 1024:
        raise ValueError('Figure exceeds its one-MiB publication budget')
    return png, {'sections': [{'name': name, 'bytes': size} for name, size in sections],
                 'word_sha256': sha(blob), 'word_bytes': len(blob),
                 'matrix_pixels_verified': 1048576, 'execution_steps': execution['steps']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--output', type=Path, help='Optional additional copy of the finished PNG')
    args = parser.parse_args(argv)
    sources = brim.demo_lctl()
    single = brim_wide.compile_chain([sources[0]])
    two = brim_wide.compile_chain(sources)
    if len(brim_wide.verify_chain(single).programs) != 1 or len(brim_wide.verify_chain(two).programs) != 2:
        raise ValueError('Single-word and two-word examples have incorrect independent counts')
    png, report = render(single)
    outputs = [(ROOT / 'examples/brim-one-word.brw', single),
               (ROOT / 'examples/brim-word-chain.brw', two),
               (ROOT / 'docs/images/brim-word.png', png)]
    if args.output:
        outputs.append((args.output, png))
    for path, data in outputs:
        store(path, data, args.replace)
    report['files'] = [{'file': path.name, 'bytes': len(data), 'sha256': sha(data)} for path, data in outputs]
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
