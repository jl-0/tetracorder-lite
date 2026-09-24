# Pinned to ubuntu:22.04 -- newer releases dropped packages DaVinci wants, such
# as libcfitsio9. No --platform pin: this image builds for linux/amd64 and
# linux/arm64, and hardcoding one here would make buildx publish that one's
# binaries under both architectures' manifest entries.

# ---------------------------------------------------------------------------
# Stage 1: build DaVinci from the vendored source in vendor/davinci.
#
# This used to be `wget` + `dpkg -i` of ASU's davinci_3.0.1-1_amd64_ubuntu22_04.deb.
# That .deb is the only binary ASU publishes and it is amd64-only, which was the
# sole reason this image was pinned to amd64 -- nothing else in the stack needs
# x86. Building from source is what makes an arm64 image possible.
#
# A separate stage keeps the compilers, headers and 10 MB of C source out of the
# finished image; only the install tree is carried forward.
# ---------------------------------------------------------------------------
FROM ubuntu:22.04 AS davinci-build

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update &&\
    apt-get install -y --no-install-suggests --no-install-recommends \
      build-essential \
      autotools-dev \
      pkg-config \
      libreadline-dev \
      libpng-dev \
      libjpeg-dev \
      zlib1g-dev \
      libcurl4-nss-dev \
      libcfitsio-dev \
      libhdf5-dev \
      dpkg-dev \
      &&\
    rm -rf /var/lib/apt/lists/*

COPY vendor/davinci/src /davinci
WORKDIR /davinci

# The tree's config.guess/config.sub are dated 2003-06-17 and predate aarch64,
# so on arm64 configure stops with "cannot guess build type". There are six of
# each -- libltdl and iomedley bundle their own -- and missing one only shows up
# later as "./configure failed for libltdl", so replace them all with Debian's
# current copies from autotools-dev.
#
# Patched here rather than in vendor/davinci/src so the vendored tree stays a
# faithful export of upstream r19810. See vendor/davinci/VENDOR.md.
RUN find . \( -name config.guess -o -name config.sub \) | \
      while read -r f; do cp "/usr/share/misc/$(basename "$f")" "$f"; done

# Five things here are load-bearing and none are obvious; vendor/davinci/VENDOR.md
# explains each with the error it produces if you get it wrong.
#
#   --without-library   ASU's .deb installs 336 MB, ~320 MB of it Mars data
#                       Tetracorder never reads. Ours installs 19 MB.
#   no --without-motif  configure.ac never guards withval="no", so negating a
#                       --with flag injects a literal -Lno/lib and kills the
#                       link of modules/thm. --without-x alone is enough.
#   --with-gplot        not --with-gnuplot, which configure --help advertises but
#                       does not register, so it is silently ignored.
#   -fPIC in CFLAGS     passing CFLAGS at all strips the -fPIC that configure.ac
#                       exports from the iomedley sub-configure. arm64-only, and
#                       the one genuine ARM blocker in this build.
#   HDF5 by CFLAGS      mandatory in practice (ff_load.c), and Ubuntu's layout
#                       does not fit --with-hdf5=<prefix>.
#
# make is serial because Makefile.am:125 uses `-ldavinci` rather than
# libdavinci.la, which gives automake no dependency edge to order against.
RUN ./configure --prefix=/usr/local \
      --without-x \
      --without-library \
      --without-examples \
      --disable-libisis \
      --with-gplot=/usr/bin/gnuplot \
      CFLAGS="-g -O2 -fPIC -I/usr/include/hdf5/serial" \
      LDFLAGS="-L/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)/hdf5/serial" &&\
    make &&\
    make install DESTDIR=/davinci-install

# ---------------------------------------------------------------------------
# Stage 2: the image itself.
# ---------------------------------------------------------------------------
FROM ubuntu:22.04

USER root
RUN apt-get update &&\
    apt-get install -y --no-install-suggests --no-install-recommends \
      #~ davinci
      wget \
      gnuplot \
      gdal-bin \
      libgdal-dev \
      libcfitsio9 \
      libcurl4-nss-dev \
      # DaVinci links these. The .deb used to pull them in as package
      # dependencies; a source build has no package metadata, so they are named
      # here. libhdf5-103-1 is the serial runtime that matches libhdf5-dev.
      libhdf5-103-1 \
      libreadline8 \
      libpng16-16 \
      libjpeg-turbo8 \
      #~ specpr
      libx11-dev \
      #~ tetracorder
      gfortran \
      make \
      gcc \
      g++ \
      ratfor \
      tcsh \
      csh \
      gnuplot \
      gnuplot-x11 \
      imagemagick \
      tgif \
      #~~ aplay
      alsa-utils \
      #~~ javac
      default-jdk \
      #~~ extras installed by the install script (do not appear to be needed, just noted here for future reference)
      # glibc-doc \
      # glibc-doc-reference \
      # libxpm-dev \
      # libxt-dev \
      # libpng-dev \
      # libjbig-dev:amd64 \
      # libjbig0:amd64 \
      # libjbig0:i386 \
      # libjbig2dec0 \
      # libjbig2dec0-dev \
      # jbig2dec \
      # jbigkit-bin \
      # libjpeg8-dev \
      # zlib1g \
      # zlib1g-dev \
      # zlib1g:i386 \
      # inotify-tools \
      # vim \
      # vim-common \
      # vim-runtime \
      # vim-tiny \
      # imagemagick \
      # imagemagick-common \
      # imagemagick-doc \
      #~ utilities
      ca-certificates \
      curl \
      git \
      &&\
    rm -rf /var/lib/apt/lists/*

WORKDIR /root

# Environment variables required for compiling specpr
#
# LD_LIBRARY_PATH names both multiarch triplets rather than just x86_64, which
# is what it used to hardcode. ENV cannot run a command, so there is no way to
# ask dpkg-architecture for the right one here; the loader simply ignores the
# entry that does not exist on the architecture being built.
ENV LD_LIBRARY_PATH="/usr/local/lib:/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu" \
    SSPPFLAGS="LINUX -INTEL -XWIN " \
    SPECPR="/root/tetracorder/specpr" \
    RANDRET="32767" \
    SP_LOCAL="/usr/local" \
    SP_BIN="securebin" \
    SP_LDFLAGS=" " \
    SP_LDLIBS="-lX11" \
    SPSDIR="syslinux" \
    RANLIB="ranlib" \
    SSPP="sspp" \
    F77="gfortran" \
    CC="cc" \
    AR="ar" \
    RF="ratfor" \
    YACC="yacc" \
    LEX="flex" \
    SP_FFLAGS="-C -O" \
    SP_FFLAGS1="-C" \
    SP_FFLAGS2="-C" \
    SPKLUDGE="LINUX" \
    BSLASH="-fno-backslash" \
    SP_FOPT="-O" \
    SP_FOPT1="-O" \
    SP_FOPT2="-O" \
    SP_RFLAGS="<" \
    SP_CFLAGS="-O -fcommon" \
    SP_ARFLAGS="rv" \
    SP_GFLAGS="-s" \
    SP_LFLAGS=" " \
    SP_YFLAGS=" " \
    LD_RUN_PATH="/usr/local/lib"

# Derived environment variables that depend on other variables
ENV SP_DBG="${SPECPR}/debug" \
    SP_TMP="${SPECPR}/tmp" \
    SP_OBJ="${SPECPR}/obj" \
    SP_LIB="${SPECPR}/lib" \
    SPSYSOBJ="${SPECPR}/obj/syslinux.o"

# DaVinci, from the source build in stage 1 rather than ASU's amd64-only .deb.
# The install tree lands on /usr/local, which LD_LIBRARY_PATH above already
# covers and which is on the default PATH -- so the colour scripts' shebang,
# `#!/usr/bin/env -S davinci -f`, resolves without further help.
COPY --from=davinci-build /davinci-install/ /
# Smoke test with the flags this build actually depends on, rather than just
# "does it start". `-V` dumps the configuration summary; `-v` is a level flag
# (-v#) that prints usage and exits 1. Asserting HDF5 and the plotting program
# here catches a silently-misconfigured build at image-build time instead of
# halfway through a run -- both have already been wrong once.
RUN ldconfig &&\
    davinci -V 2>&1 | sed 's/\\n/\n/g' > /tmp/dv-config &&\
    cat /tmp/dv-config &&\
    grep -q "Version #3" /tmp/dv-config &&\
    grep -qE "^ *HDF5: *Yes" /tmp/dv-config &&\
    grep -qE "^ *Readline: *Yes" /tmp/dv-config &&\
    grep -qE "^ *Module Support: *Yes" /tmp/dv-config &&\
    grep -qE "^Plotting program: */usr/bin/gnuplot" /tmp/dv-config &&\
    rm /tmp/dv-config

# Initialize tetracorder
COPY . .
RUN sed -i "s/rclark/root/g" tetracorder/AAA.INSTALL.spectroscopy-os-setup-linux.sh &&\
    sed -i "s/home/root/g" tetracorder/AAA.INSTALL.spectroscopy-os-setup-linux.sh &&\
    ln -s tetracorder local &&\
    ln -s tetracorder/sl1 sl1 &&\
    mkdir t1 && ln -s /root/tetracorder/tetracorder.cmds t1/tetracorder.cmds

# Make available Davinci-Tetracorder commands
ENV PATH="/root/tetracorder/tetracorder.cmds/tetracorder6.00a.cmds/davinci-cmds.for.usr.local.bin/:$PATH"

# Install specpr
RUN cd tetracorder/specpr &&\
    mkdir -p lib obj &&\
    # src.specpr errors about ratfor (??), manually making seems to fix it
    cd src.specpr/common && make && cd - &&\
    # psplotdaemon does not compile due to unresolved errors, skip it
    sed -i "234,245 s/^/#/" AAA.INSTALL.specpr+support-progs-linux-upgrade.1.7.sh &&\
    yes "" | bash AAA.INSTALL.specpr+support-progs-linux-upgrade.1.7.sh install

# Install tetracorder
#
# Tetracorder's makefile hardcodes `mcmodelflags=-mcmodel=medium`, which only
# exists on x86-64; aarch64 gfortran rejects it outright:
#   gfortran: error: unrecognized argument in option '-mcmodel=medium'
#
# The flag is there for a specifically x86-64 reason, documented in multmap.h:
# the default model caps static arrays at 2 GB, and with maxmat=670 Tetracorder's
# COMMON blocks came close enough to hit
#   relocation truncated to fit: R_X86_64_PC32 against symbol `lblg_'
# aarch64's default (small) model addresses 4 GB, twice x86-64's, so dropping the
# flag there keeps more headroom than x86-64 has with it. If Tetracorder ever
# does outgrow that, the linker says so loudly rather than miscompiling -- watch
# for the aarch64 spelling of the same "relocation truncated to fit" error, and
# reach for -mcmodel=large then. It is not used now because specpr.a is built
# without a model flag and mixing large with small can fail to link.
RUN cd tetracorder &&\
    if [ "$(uname -m)" != x86_64 ]; then \
      sed -i "s/^mcmodelflags=.*/mcmodelflags=/" tetracorder/makefile; \
      echo "dropped -mcmodel=medium (not an option on $(uname -m))"; \
    fi &&\
    # Comment out the chown/chmod section (causes an error on some systems using network mounted filesystems)
    sed -i "398,416 s/^/#/" AAA.INSTALL.spectroscopy-os-setup-linux.sh &&\
    # Comment out forced installs
    sed -i "231,254 s/^/#/" AAA.INSTALL.spectroscopy-os-setup-linux.sh &&\
    yes "y" | bash AAA.INSTALL.spectroscopy-os-setup-linux.sh install &&\
    # Build tetracorder
    cd tetracorder &&\
    ## Build cube spectrum mode
    make install &&\
    ## Build single spectrum mode
    ### Disable block A, enable block B parameter settings
    sed -i "137,140 s/^/#/" multmap.h &&\
    sed -i "144,147 s/^#//" multmap.h &&\
    make installsingle

# Setup the python environment
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"
RUN uv sync --all-extras
ENV PATH="/root/.venv/bin/:$PATH"

CMD ["tetrapy"]
