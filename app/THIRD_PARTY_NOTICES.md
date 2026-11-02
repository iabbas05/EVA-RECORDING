# Bundled runtime notices

EVA's Python source is included with this release. Its Windows executable bundles Python 3.12 and the packages listed in requirements.lock.txt. Each component retains its own license; the executable is packaged using PyInstaller's executable-bundling exception.

Primary projects and source/license locations:

- Python — https://www.python.org/downloads/source/ — PSF License.
- PyInstaller — https://github.com/pyinstaller/pyinstaller — GPL with exception for generated executables.
- pywebview — https://github.com/r0x0r/pywebview — BSD-3-Clause.
- Playwright — https://github.com/microsoft/playwright-python — Apache-2.0.
- obsws-python — https://github.com/aatikturk/obsws-python — MIT.
- websocket-client — https://github.com/websocket-client/websocket-client — Apache-2.0.
- keyring — https://github.com/jaraco/keyring — MIT.
- Google API Python client — https://github.com/googleapis/google-api-python-client — Apache-2.0.
- Google Auth/OAuth — https://github.com/googleapis/google-auth-library-python and https://github.com/googleapis/google-auth-library-python-oauthlib — Apache-2.0.
- Pillow — https://github.com/python-pillow/Pillow — HPND.
- PyAV 16.1.0 — https://github.com/PyAV-Org/PyAV/tree/v16.1.0 — BSD-3-Clause. Its published Windows wheel includes FFmpeg shared libraries. FFmpeg sources and licenses: https://ffmpeg.org/download.html and https://ffmpeg.org/legal.html. The wheel's build sources are maintained at https://github.com/PyAV-Org/pyav-wheels.

OBS Studio, Streamlabs Desktop, Google Chrome, and Microsoft Edge WebView2 are external applications/runtime components and are not redistributed in this ZIP.

The pinned requirements file identifies the exact bundled versions and transitive dependencies. Distribution metadata/license files included by package hooks remain part of the bundled runtime where provided by upstream wheels.

## PyAV license text

Copyright retained by original committers. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the project nor the names of its contributors may be
      used to endorse or promote products derived from this software without
      specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Official EVA wordmark: original SVG path from https://www.eva.gg/en-US website header (SvgEva), retrieved 2026-10-06. Original geometry retained; website telemetry attributes removed. EVA branding belongs to its respective owner.
