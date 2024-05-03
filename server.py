#!/usr/bin/env python3
"""
weasyprint server

A tiny aiohttp based web server that wraps weasyprint
It expects a multipart/form-data upload containing an html file, an optional
css file and optional attachments.
"""
from aiohttp import web
from urllib.parse import urlparse
from weasyprint import CSS, HTML, default_url_fetcher
from weasyprint.text.fonts import FontConfiguration
import logging
import os.path
import tempfile
import sys
import shutil
import os

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger('weasyprint-server')

CHUNK_SIZE = 65536

temp_dir = tempfile.mkdtemp()

class URLFetcher:
    """URL fetcher that only allows data URLs and known files"""
    def __init__(self, valid_paths):
        self.valid_paths = valid_paths

    def __call__(self, url):
        parsed = urlparse(url)

        if parsed.scheme == 'data':
            return default_url_fetcher(url)

        if parsed.scheme in ['', 'file'] and parsed.path:
            if os.path.abspath(parsed.path) in self.valid_paths:
                return default_url_fetcher(url)
            else:
                raise ValueError('Only known path allowed')

        raise ValueError('External resources are not allowed')


async def render_pdf(request):
    logger.info("Received request for PDF rendering.")
    form_data = {}
    temp_dir = None

    reader = await request.multipart()
    logger.info("Processing multipart data.")

    with tempfile.TemporaryDirectory() as temp_dir:
        while True:
            part = await reader.next()

            if part is None:
                break

            logger.info(f"Processing part: {part.name}")
            if (
                part.name in ['html', 'css']
                or part.name.startswith('attachment.')
                or part.name.startswith('asset.')
            ):
                form_data[part.name] = await save_part_to_file(part, temp_dir)

        logger.info(f"Form data processed: {list(form_data.keys())}")

        font_config = FontConfiguration()

        if 'html' not in form_data:
            logger.info('Bad request. No html file provided.')
            return web.Response(status=400, text="No html file provided.")

        html = HTML(filename=form_data['html'], url_fetcher=URLFetcher(form_data.values()))
        if 'css' in form_data:
            css = CSS(filename=form_data['css'], url_fetcher=URLFetcher(form_data.values()))
        else:
            # Add custom fonts, see https://stackoverflow.com/a/55134862/1348352
            css = CSS(string='''
                @font-face {
                    font-family: "Zeitung Micro Pro";
                    src: url(file:///home/user/project/Zeitung-Micro-Pro.ttf) format("truetype");
                }
                @font-face {
                    font-family: "Tisa Pro";
                    src: url(file:///home/user/project/Tisa-Pro.ttf) format("truetype");
                }
            ''', font_config=font_config)

        attachments = [
            attachment for name, attachment in form_data.items()
            if name.startswith('attachment.')
        ]

        pdf_filename = os.path.join(temp_dir, 'output.pdf')

        try:
            html.write_pdf(pdf_filename, stylesheets=[css], font_config=font_config, attachments=attachments)
        except Exception:
            logger.exception('PDF generation failed')
            return web.Response(status=500, text="PDF generation failed.")
        else:
            return await stream_file(request, pdf_filename, 'application/pdf')

async def save_part_to_file(part, directory):
    filepath = os.path.join(directory, part.filename)
    with open(filepath, 'wb') as file_:
        while True:
            chunk = await part.read_chunk(CHUNK_SIZE)
            if not chunk:
                break
            file_.write(chunk)

    # Ensure the file exists
    if not os.path.exists(filepath):
        logger.error("File not found after writing to " + filepath)

    logger.info(f'Saved part "{part.name}" to "{filepath}"')
    return filepath


async def stream_file(request, filename, content_type):
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': content_type,
            'Content-Disposition':
            f'attachment; filename="{os.path.basename(filename)}"',
        },
    )

    await response.prepare(request)

    with open(filename, 'rb') as outfile:
        while True:
            data = outfile.read(CHUNK_SIZE)
            if not data:
                break
            await response.write(data)

    await response.write_eof()

    return response


async def healthcheck(request):
    return web.Response(status=200, text="OK")

if __name__ == '__main__':
    app = web.Application()
    app.add_routes([web.post('/', render_pdf)])
    app.add_routes([web.get('/healthcheck', healthcheck)])
    web.run_app(app)
