# Dusky Audio Studio — Native DSP Engine Build
# Builds locally against the installed PipeWire and RNNoise packages

CC      ?= cc
STRIP   ?= strip --strip-all

CFLAGS  ?= -O2 -pipe -fno-plt -fexceptions -fstack-clash-protection -fcf-protection -fno-strict-aliasing -flto=auto -Wall -Wextra
CFLAGS  += -std=gnu23 -I.
# Keep the default binary portable for ISO distribution. Local installations
# may opt in with HOST_CFLAGS="-march=native -mtune=native".
HOST_CFLAGS ?=

PW_CFLAGS = $(shell pkg-config --cflags libpipewire-0.3 rnnoise)
PW_LIBS   = $(shell pkg-config --libs libpipewire-0.3 rnnoise)

USE_MOLD ?= $(shell command -v mold >/dev/null 2>&1 && echo 1 || echo 0)
ifeq ($(USE_MOLD),1)
MOLD_FLAG = -fuse-ld=mold
else
MOLD_FLAG =
endif

LDLIBS  += $(PW_LIBS) -lm -lpthread
LDFLAGS += -Wl,-O1 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now -Wl,-z,pack-relative-relocs $(MOLD_FLAG) -flto=auto

.DELETE_ON_ERROR:

all: dusky_audio_dsp

dusky_audio_dsp: main.c protocol.h Makefile
	$(CC) $(CFLAGS) $(PW_CFLAGS) $(HOST_CFLAGS) $(LDFLAGS) main.c $(LDLIBS) -o $@
	$(STRIP) $@

clean:
	rm -f dusky_audio_dsp *.o

.PHONY: all clean
