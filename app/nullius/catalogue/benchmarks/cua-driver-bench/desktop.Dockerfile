# The bench's Linux desktop from the pins alone, and the port's Python beside it.
# Stage `bench`: Cua's public Linux image (XFCE 4.18 on Xvfb at 1920 x 1080, desktop user `cua`) at a digest; the
# bench's folder at the entry's pin, given as the build context's `bench/` from the upstream's checkout, placed as
# `cua`'s checkout; then the bench's two provisioning scripts, unchanged, as root and as `cua`.
# The last stage: the port's Python, a venv on the image's Python with the image's packages visible, holding
# desktop-requirements.txt by hash; the bench's own Python is not changed. BENCH_IMAGE names another image to put
# that one layer on, for example one the bench's scripts built already; the stage `bench` is then not built.
ARG BENCH_IMAGE=bench

FROM ghcr.io/trycua/linux:24.04@sha256:6b37972e97bcaeea16fc8872dd99ebc72ef99c4b886b000545b28f6d470b8a48 AS bench
USER root
RUN install -d -o 1000 -g 1000 /home/cua/cua /home/cua/cua/libs
COPY --chown=1000:1000 bench /home/cua/cua/libs/cua-driver-bench
RUN /home/cua/cua/libs/cua-driver-bench/automated-eval/macos_bench/launch/xfce/provision-root.sh
USER 1000:1000
# the image sets HOME to root's; `npx electron --version` in provision-user.sh aborts on Electron's sandbox in a
# container, and Electron's own variable turns that sandbox off for the call
RUN cd /home/cua \
 && HOME=/home/cua ELECTRON_DISABLE_SANDBOX=1 \
      /home/cua/cua/libs/cua-driver-bench/automated-eval/macos_bench/launch/xfce/provision-user.sh \
 && rm -rf /home/cua/.npm /home/cua/.cache/electron
USER root

FROM ${BENCH_IMAGE}
USER root
COPY desktop-requirements.txt /opt/nullius/computer-requirements.txt
# the sdists among the pins build on the image's own setuptools and wheel, seen through the venv.
# The display has no authority file, and python-xlib refuses to connect without one, or prints a warning on
# standard output for an empty one: the port's code is given one entry that matches no display.
RUN python3 -m venv --system-site-packages /opt/nullius/computer \
 && /opt/nullius/computer/bin/pip install --no-cache-dir --no-deps --no-build-isolation --require-hashes \
      -r /opt/nullius/computer-requirements.txt \
 && /opt/nullius/computer/bin/python3 -I -c "import PIL.ImageGrab, Xlib, pyscreeze, pymsgbox, pytweening" \
 && /opt/nullius/computer/bin/python3 -I -c "import struct; f = lambda b: struct.pack('>H', len(b)) + b; \
open('/opt/nullius/computer/Xauthority', 'wb').write(struct.pack('>H', 256) + f(b'none') + f(b'none') \
+ f(b'MIT-MAGIC-COOKIE-1') + f(bytes(16)))" \
 && chmod 0644 /opt/nullius/computer/Xauthority
