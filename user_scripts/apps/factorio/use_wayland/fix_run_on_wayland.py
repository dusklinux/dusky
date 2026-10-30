#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_run_on_wayland.py — Factorio 2.1.x "Failed to create shader" native-Wayland fix.

SELF-CONTAINED: this single file embeds the EGL interposer source (eglfix.c),
the linker version script (export.map) and a GL/EGL smoke test. It builds the
shim, installs it into a persistent location, wires the jc141 launcher config
and verifies everything empirically. Package installs auto-elevate to sudo
(interactive password prompt) only when needed.

Why this exists
---------------
Factorio 2.1.14 crashes ~1s after launch on a native Wayland session (no
XWayland) with:

    Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.

Root cause (empirically verified): on the Wayland/EGL path the game loses EGL
context currency on the main thread between GL init and the first shader
compile. At the first glCreateShader, eglGetCurrentContext()==NULL, every GL
call fails silently, glCreateShader returns 0 and the game aborts. The X11
path works; native Wayland doesn't. The game dlsyms EGL itself, so the only
way to intercept is a libEGL.so.1 interposer (same SONAME) loaded via
LD_PRELOAD. It forwards all EGL calls to the real glvnd library, remembers the
last good eglMakeCurrent binding, and re-binds it on the calling thread right
before the first glCreateShader/glCreateProgram.

Where the shim lives
--------------------
The generated fix is installed to:

    ~/.factorio/wayland_fix/libEGL.so.1        (used for non-sandboxed runs)
    <JC_DIRECTORY>/native-docs/.factorio/wayland_fix/libEGL.so.1
                                               (used inside the bubblewrap
                                                sandbox, which maps /home/<you>
                                                -> native-docs)

local.config references `$HOME/.factorio/wayland_fix/libEGL.so.1`; the shell
expands $HOME when sourcing, so the one line resolves correctly in BOTH launch
modes. This location is persistent (survives re-downloads of the game repack),
unlike the old in-game `eglfix/` dir which this script now removes.

Usage
-----
    python3 fix_run_on_wayland.py [--game-dir /path/to/Factorio_2.1.14]
    python3 fix_run_on_wayland.py --check                # inspect only
    python3 fix_run_on_wayland.py --force                # force shim rebuild
    python3 fix_run_on_wayland.py --verify-game          # also launch the game
    python3 fix_run_on_wayland.py --reset                # remove the fix
    python3 fix_run_on_wayland.py --troubleshoot         # full diagnostic report

Exit codes: 0 = ok, 1 = error, 2 = check found problems.
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# --------------------------------------------------------------------------
# Rich console (optional; plain-text fallback if python-rich is absent)
# --------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    RICH = True
except Exception:  # pragma: no cover
    RICH = False

# --------------------------------------------------------------------------
# Embedded sources (generated at build time; do not edit by hand)
# --------------------------------------------------------------------------
EGLFIX_C = '/*\n * eglfix.c — libEGL.so.1 interposer for Factorio 2.1.x native-Wayland crash.\n *\n * Problem: at first shader compile, eglGetCurrentContext() == NULL on the\n * main thread even though the context was bound successfully during init.\n * Every GL call then fails silently (glCreateShader -> 0) and Factorio\n * aborts with "Failed to create shader".\n *\n * This shim is loaded via LD_PRELOAD and declares SONAME libEGL.so.1, so\n * any dlopen("libEGL.so.1") by the game or SDL returns THIS library. We\n * forward every EGL call to the real libEGL, remember the last successful\n * eglMakeCurrent binding, and right before the game\'s first\n * glCreateShader/glCreateProgram call, if the calling thread has no current\n * context, we re-bind the remembered (display, surface, context).\n *\n * Build:\n *   gcc -shared -fPIC -O2 -o libEGL.so.1 eglfix.c -ldl -lpthread \\\n *       -Wl,-soname,libEGL.so.1\n */\n#define _GNU_SOURCE\n#include <EGL/egl.h>\n#include <EGL/eglext.h>\n#include <dlfcn.h>\n#include <pthread.h>\n#include <stdarg.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <time.h>\n#include <unistd.h>\n\n/* Minimal GL types (EGL headers do not pull these in) */\ntypedef unsigned int GLenum;\ntypedef unsigned int GLuint;\ntypedef int GLsizei;\ntypedef int GLint;\ntypedef unsigned int GLbitfield;\ntypedef long GLsizeiptr;\ntypedef long GLintptr;\ntypedef char GLchar;\ntypedef EGLBoolean(*real_eglMakeCurrent_t)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);\ntypedef EGLContext(*real_eglGetCurrentContext_t)(void);\ntypedef EGLint(*real_eglGetError_t)(void);\ntypedef GLuint(*real_glCreateShader_t)(GLenum);\ntypedef GLuint(*real_glCreateProgram_t)(void);\ntypedef __eglMustCastToProperFunctionPointerType(*real_eglGetProcAddress_t)(const char *);\n\n/* forward decls used by the gl* wrappers below */\nstatic void fx_ensure_before_gl(const char *what);\nstatic unsigned int fx_check_gl_after(const char *what, void *retaddr);\n\n/* ------------------------------------------------------------------ */\n/* logging                                                             */\n/* ------------------------------------------------------------------ */\nstatic double fx_now_ms(void) {\n    struct timespec ts;\n    clock_gettime(CLOCK_MONOTONIC, &ts);\n    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;\n}\n\nstatic void fx_log(const char *fmt, ...) {\n    FILE *f = fopen("/tmp/eglfix.log", "a");\n    if (!f) return;\n    fprintf(f, "[eglfix t=%ld %.1fms] ", (long)gettid(), fx_now_ms());\n    va_list ap;\n    va_start(ap, fmt);\n    vfprintf(f, fmt, ap);\n    va_end(ap);\n    fputc(\'\\n\', f);\n    fclose(f);\n}\n\n/* ------------------------------------------------------------------ */\n/* real library resolution                                             */\n/* ------------------------------------------------------------------ */\nstatic void *real_lib = NULL;\ntypedef void *(*real_dlsym_func_t)(void *, const char *);\nstatic real_dlsym_func_t libc_dlsym = NULL;\n\nstatic void *sym(const char *name) {\n    if (!real_lib) return NULL;\n    if (!libc_dlsym) {\n        libc_dlsym = (real_dlsym_func_t)dlvsym(RTLD_DEFAULT, "dlsym", "GLIBC_2.34");\n        if (!libc_dlsym) libc_dlsym = (real_dlsym_func_t)dlvsym(RTLD_DEFAULT, "dlsym", "GLIBC_2.2.5");\n        if (!libc_dlsym) libc_dlsym = (real_dlsym_func_t)dlsym;\n    }\n    return libc_dlsym(real_lib, name);\n}\n\n/* real_eglGetProcAddress returns __eglMustCastToProperFunctionPointerType;\n * use a helper that casts to void* for our own use. */\nstatic void *sym_proc(const char *name) {\n    if (!real_lib) return NULL;\n    real_eglGetProcAddress_t r = (real_eglGetProcAddress_t)sym("eglGetProcAddress");\n    if (!r) return NULL;\n    return (void *)r(name);\n}\n\nstatic pthread_once_t g_resolve_once = PTHREAD_ONCE_INIT;\n\nstatic void do_resolve(void) {\n    /* Prefer the real glvnd dispatcher. dlopen by ABSOLUTE PATH so we don\'t\n     * get ourselves back (we match by SONAME, not by path). */\n    real_lib = dlopen("/usr/lib/libEGL.so.1", RTLD_NOW | RTLD_LOCAL);\n    if (!real_lib) real_lib = dlopen("/usr/lib64/libEGL.so.1", RTLD_NOW | RTLD_LOCAL);\n    if (!real_lib) {\n        /* fall back to the Mesa vendor lib (exports the full EGL API) */\n        real_lib = dlopen("libEGL_mesa.so.0", RTLD_NOW | RTLD_LOCAL);\n    }\n    if (!real_lib) {\n        fx_log("FATAL: could not dlopen real libEGL: %s", dlerror());\n        return;\n    }\n    /* Guard: if we somehow got ourselves, bail loudly. */\n    void *probe = sym("eglGetProcAddress");\n    if (probe == (void *)&eglGetProcAddress) {\n        fx_log("FATAL: dlopen returned OUR shim (recursion). dlerror=%s", dlerror());\n        real_lib = NULL;\n        return;\n    }\n    fx_log("real libEGL loaded: %p", real_lib);\n}\n\nstatic void resolve(void) {\n    pthread_once(&g_resolve_once, do_resolve);\n}\n\n/* ------------------------------------------------------------------ */\n/* remembered binding                                                  */\n/* ------------------------------------------------------------------ */\nstatic pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;\nstatic EGLDisplay g_disp = EGL_NO_DISPLAY;\nstatic EGLContext g_ctx = EGL_NO_CONTEXT;\nstatic EGLSurface g_draw = EGL_NO_SURFACE;\nstatic EGLSurface g_read = EGL_NO_SURFACE;\nstatic int g_surface_alive = 0; /* recorded draw surface not yet destroyed */\nstatic int g_ensured = 0;       /* one-shot: auto-rebind already done for current binding */\n\nstatic real_eglMakeCurrent_t R_eglMakeCurrent = NULL;\nstatic real_eglGetCurrentContext_t R_eglGetCurrentContext = NULL;\nstatic real_eglGetError_t R_eglGetError = NULL;\n\nstatic void ensure_current(void) {\n    resolve();\n    if (!R_eglMakeCurrent || !R_eglGetCurrentContext) {\n        R_eglMakeCurrent = (real_eglMakeCurrent_t)sym("eglMakeCurrent");\n        R_eglGetCurrentContext = (real_eglGetCurrentContext_t)sym("eglGetCurrentContext");\n        R_eglGetError = (real_eglGetError_t)sym("eglGetError");\n    }\n    if (!R_eglMakeCurrent || !g_ctx) return;\n\n    if (R_eglGetCurrentContext() != EGL_NO_CONTEXT) return; /* already current */\n\n    pthread_mutex_lock(&g_lock);\n    EGLDisplay d = g_disp;\n    EGLContext c = g_ctx;\n    EGLSurface s = g_surface_alive ? g_draw : EGL_NO_SURFACE;\n    int done = g_ensured;\n    pthread_mutex_unlock(&g_lock);\n\n    if (done || !d || !c) return;\n    EGLBoolean ok = R_eglMakeCurrent(d, s, s, c);\n    EGLint err = R_eglGetError();\n    fx_log("ensure_current: rebind d=%p s=%p c=%p -> %d err=0x%x", d, s, c, ok, err);\n    if (ok != EGL_TRUE && s != EGL_NO_SURFACE) {\n        EGLBoolean ok2 = R_eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, c);\n        fx_log("ensure_current: surfaceless retry -> %d err=0x%x", ok2, R_eglGetError());\n    }\n    if (ok == EGL_TRUE || s == EGL_NO_SURFACE) {\n        /* one-shot: only auto-rebind once per recorded binding; a new\n         * binding/context (eglMakeCurrent/eglCreateContext) resets this. */\n        pthread_mutex_lock(&g_lock);\n        g_ensured = 1;\n        pthread_mutex_unlock(&g_lock);\n    }\n}\n\n/* ------------------------------------------------------------------ */\n/* wrapped GL entry points (the first GL calls after context loss)     */\n/* ------------------------------------------------------------------ */\nstatic real_glCreateShader_t R_glCreateShader = NULL;\nstatic real_glCreateProgram_t R_glCreateProgram = NULL;\n\nGLuint glCreateShader(GLenum type) {\n    resolve();\n    if (!R_glCreateShader) R_glCreateShader = (real_glCreateShader_t)sym_proc("glCreateShader");\n    ensure_current();\n    if (!R_glCreateShader) return 0;\n    return R_glCreateShader(type);\n}\n\nGLuint glCreateProgram(void) {\n    resolve();\n    if (!R_glCreateProgram) R_glCreateProgram = (real_glCreateProgram_t)sym_proc("glCreateProgram");\n    ensure_current();\n    if (!R_glCreateProgram) return 0;\n    return R_glCreateProgram();\n}\n\n/* ------------------------------------------------------------------ */\n/* glGetError + texture-call wrappers (diagnostics + ensure)           */\n/* ------------------------------------------------------------------ */\ntypedef unsigned int (*real_glGetError_t)(void);\nstatic real_glGetError_t R_glGetErrorGL = NULL;\n\ntypedef void (*real_glGenTextures_t)(GLsizei, unsigned int *);\nstatic real_glGenTextures_t R_glGenTextures = NULL;\ntypedef void (*real_glBindTexture_t)(GLenum, unsigned int);\nstatic real_glBindTexture_t R_glBindTexture = NULL;\ntypedef void (*real_glTexImage2D_t)(GLenum, int, int, GLsizei, GLsizei, int, GLenum, GLenum, const void *);\nstatic real_glTexImage2D_t R_glTexImage2D = NULL;\ntypedef void (*real_glGenBuffers_t)(GLsizei, unsigned int *);\nstatic real_glGenBuffers_t R_glGenBuffers = NULL;\ntypedef void (*real_glBufferData_t)(GLenum, long, const void *, GLenum);\nstatic real_glBufferData_t R_glBufferData = NULL;\ntypedef void (*real_glClear_t)(GLenum);\nstatic real_glClear_t R_glClear = NULL;\ntypedef EGLBoolean(*real_eglQuerySurface_t)(EGLDisplay, EGLSurface, EGLint, EGLint *);\nstatic real_eglQuerySurface_t R_eglQuerySurface = NULL;\ntypedef void (*real_glDrawArrays_t)(GLenum, int, GLsizei);\nstatic real_glDrawArrays_t R_glDrawArrays = NULL;\ntypedef void (*real_glDrawElements_t)(GLenum, GLsizei, GLenum, const void *);\nstatic real_glDrawElements_t R_glDrawElements = NULL;\ntypedef void (*real_glUseProgram_t)(unsigned int);\nstatic real_glUseProgram_t R_glUseProgram = NULL;\ntypedef void (*real_glBindVertexArray_t)(unsigned int);\nstatic real_glBindVertexArray_t R_glBindVertexArray = NULL;\ntypedef void (*real_glEnable_t)(GLenum);\nstatic real_glEnable_t R_glEnable = NULL;\ntypedef void (*real_glDisable_t)(GLenum);\nstatic real_glDisable_t R_glDisable = NULL;\n\nvoid glDrawArrays(GLenum mode, int first, GLsizei count) {\n    resolve();\n    if (!R_glDrawArrays) R_glDrawArrays = (real_glDrawArrays_t)sym_proc("glDrawArrays");\n    fx_ensure_before_gl("glDrawArrays");\n    if (R_glDrawArrays) R_glDrawArrays(mode, first, count);\n    fx_check_gl_after("glDrawArrays", __builtin_return_address(0));\n}\n\nvoid glDrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices) {\n    resolve();\n    if (!R_glDrawElements) R_glDrawElements = (real_glDrawElements_t)sym_proc("glDrawElements");\n    fx_ensure_before_gl("glDrawElements");\n    if (R_glDrawElements) R_glDrawElements(mode, count, type, indices);\n    fx_check_gl_after("glDrawElements", __builtin_return_address(0));\n}\n\nvoid glUseProgram(unsigned int program) {\n    resolve();\n    if (!R_glUseProgram) R_glUseProgram = (real_glUseProgram_t)sym_proc("glUseProgram");\n    fx_ensure_before_gl("glUseProgram");\n    if (R_glUseProgram) R_glUseProgram(program);\n    fx_check_gl_after("glUseProgram", __builtin_return_address(0));\n}\n\nvoid glBindVertexArray(unsigned int array) {\n    resolve();\n    if (!R_glBindVertexArray) R_glBindVertexArray = (real_glBindVertexArray_t)sym_proc("glBindVertexArray");\n    fx_ensure_before_gl("glBindVertexArray");\n    if (R_glBindVertexArray) R_glBindVertexArray(array);\n    fx_check_gl_after("glBindVertexArray", __builtin_return_address(0));\n}\n\nvoid glEnable(GLenum cap) {\n    resolve();\n    if (!R_glEnable) R_glEnable = (real_glEnable_t)sym_proc("glEnable");\n    fx_ensure_before_gl("glEnable");\n    if (R_glEnable) R_glEnable(cap);\n    fx_check_gl_after("glEnable", __builtin_return_address(0));\n}\n\ntypedef void (*real_glViewport_t)(int, int, GLsizei, GLsizei);\nstatic real_glViewport_t R_glViewport = NULL;\ntypedef void (*real_glTexSubImage2D_t)(GLenum, int, int, int, GLsizei, GLsizei, GLenum, GLenum, const void *);\nstatic real_glTexSubImage2D_t R_glTexSubImage2D = NULL;\ntypedef void (*real_glCompressedTexImage2D_t)(GLenum, int, GLenum, GLsizei, GLsizei, int, GLsizei, const void *);\nstatic real_glCompressedTexImage2D_t R_glCompressedTexImage2D = NULL;\ntypedef void (*real_glTexStorage2D_t)(GLenum, int, GLenum, GLsizei, GLsizei);\nstatic real_glTexStorage2D_t R_glTexStorage2D = NULL;\ntypedef void (*real_glGenerateMipmap_t)(GLenum);\nstatic real_glGenerateMipmap_t R_glGenerateMipmap = NULL;\ntypedef void (*real_glPixelStorei_t)(GLenum, int);\nstatic real_glPixelStorei_t R_glPixelStorei = NULL;\n\ntypedef void (*real_glCreateTextures_t)(GLenum, GLsizei, unsigned int *);\nstatic real_glCreateTextures_t R_glCreateTextures = NULL;\ntypedef void (*real_glTextureStorage2D_t)(unsigned int, int, GLenum, GLsizei, GLsizei);\nstatic real_glTextureStorage2D_t R_glTextureStorage2D = NULL;\ntypedef void (*real_glTextureSubImage2D_t)(unsigned int, int, int, int, GLsizei, GLsizei, GLenum, GLenum, const void *);\nstatic real_glTextureSubImage2D_t R_glTextureSubImage2D = NULL;\ntypedef void (*real_glBindFramebuffer_t)(GLenum, unsigned int);\nstatic real_glBindFramebuffer_t R_glBindFramebuffer = NULL;\ntypedef void (*real_glFramebufferTexture2D_t)(GLenum, GLenum, GLenum, unsigned int, int);\nstatic real_glFramebufferTexture2D_t R_glFramebufferTexture2D = NULL;\n\nvoid glViewport(int x, int y, GLsizei width, GLsizei height) {\n    resolve();\n    if (!R_glViewport) R_glViewport = (real_glViewport_t)sym_proc("glViewport");\n    fx_ensure_before_gl("glViewport");\n    if (R_glViewport) R_glViewport(x, y, width, height);\n}\n\nvoid glTexSubImage2D(GLenum target, int level, int xoffset, int yoffset, GLsizei width, GLsizei height, GLenum format, GLenum type, const void *pixels) {\n    resolve();\n    if (!R_glTexSubImage2D) R_glTexSubImage2D = (real_glTexSubImage2D_t)sym_proc("glTexSubImage2D");\n    fx_ensure_before_gl("glTexSubImage2D");\n    if (R_glTexSubImage2D) R_glTexSubImage2D(target, level, xoffset, yoffset, width, height, format, type, pixels);\n    fx_check_gl_after("glTexSubImage2D", __builtin_return_address(0));\n}\n\nvoid glCompressedTexImage2D(GLenum target, int level, GLenum internalformat, GLsizei width, GLsizei height, int border, GLsizei imageSize, const void *data) {\n    resolve();\n    if (!R_glCompressedTexImage2D) R_glCompressedTexImage2D = (real_glCompressedTexImage2D_t)sym_proc("glCompressedTexImage2D");\n    fx_ensure_before_gl("glCompressedTexImage2D");\n    if (R_glCompressedTexImage2D) R_glCompressedTexImage2D(target, level, internalformat, width, height, border, imageSize, data);\n    fx_check_gl_after("glCompressedTexImage2D", __builtin_return_address(0));\n}\n\nvoid glTexStorage2D(GLenum target, int levels, GLenum internalformat, GLsizei width, GLsizei height) {\n    resolve();\n    if (!R_glTexStorage2D) R_glTexStorage2D = (real_glTexStorage2D_t)sym_proc("glTexStorage2D");\n    fx_ensure_before_gl("glTexStorage2D");\n    if (R_glTexStorage2D) R_glTexStorage2D(target, levels, internalformat, width, height);\n    fx_check_gl_after("glTexStorage2D", __builtin_return_address(0));\n}\n\nvoid glGenerateMipmap(GLenum target) {\n    resolve();\n    if (!R_glGenerateMipmap) R_glGenerateMipmap = (real_glGenerateMipmap_t)sym_proc("glGenerateMipmap");\n    fx_ensure_before_gl("glGenerateMipmap");\n    if (R_glGenerateMipmap) R_glGenerateMipmap(target);\n    fx_check_gl_after("glGenerateMipmap", __builtin_return_address(0));\n}\n\nvoid glPixelStorei(GLenum pname, int param) {\n    resolve();\n    if (!R_glPixelStorei) R_glPixelStorei = (real_glPixelStorei_t)sym_proc("glPixelStorei");\n    fx_ensure_before_gl("glPixelStorei");\n    if (R_glPixelStorei) R_glPixelStorei(pname, param);\n    fx_check_gl_after("glPixelStorei", __builtin_return_address(0));\n}\n\nvoid glCreateTextures(GLenum target, GLsizei n, unsigned int *textures) {\n    resolve();\n    if (!R_glCreateTextures) R_glCreateTextures = (real_glCreateTextures_t)sym_proc("glCreateTextures");\n    fx_ensure_before_gl("glCreateTextures");\n    if (R_glCreateTextures) R_glCreateTextures(target, n, textures);\n    fx_check_gl_after("glCreateTextures", __builtin_return_address(0));\n}\n\nvoid glTextureStorage2D(unsigned int texture, int levels, GLenum internalformat, GLsizei width, GLsizei height) {\n    resolve();\n    if (!R_glTextureStorage2D) R_glTextureStorage2D = (real_glTextureStorage2D_t)sym_proc("glTextureStorage2D");\n    fx_ensure_before_gl("glTextureStorage2D");\n    if (R_glTextureStorage2D) R_glTextureStorage2D(texture, levels, internalformat, width, height);\n    fx_check_gl_after("glTextureStorage2D", __builtin_return_address(0));\n}\n\nvoid glTextureSubImage2D(unsigned int texture, int level, int xoffset, int yoffset, GLsizei width, GLsizei height, GLenum format, GLenum type, const void *pixels) {\n    resolve();\n    if (!R_glTextureSubImage2D) R_glTextureSubImage2D = (real_glTextureSubImage2D_t)sym_proc("glTextureSubImage2D");\n    fx_ensure_before_gl("glTextureSubImage2D");\n    if (R_glTextureSubImage2D) R_glTextureSubImage2D(texture, level, xoffset, yoffset, width, height, format, type, pixels);\n    fx_check_gl_after("glTextureSubImage2D", __builtin_return_address(0));\n}\n\nvoid glBindFramebuffer(GLenum target, unsigned int framebuffer) {\n    resolve();\n    if (!R_glBindFramebuffer) R_glBindFramebuffer = (real_glBindFramebuffer_t)sym_proc("glBindFramebuffer");\n    fx_ensure_before_gl("glBindFramebuffer");\n    if (R_glBindFramebuffer) R_glBindFramebuffer(target, framebuffer);\n    fx_check_gl_after("glBindFramebuffer", __builtin_return_address(0));\n}\n\nvoid glFramebufferTexture2D(GLenum target, GLenum attachment, GLenum textarget, unsigned int texture, int level) {\n    resolve();\n    if (!R_glFramebufferTexture2D) R_glFramebufferTexture2D = (real_glFramebufferTexture2D_t)sym_proc("glFramebufferTexture2D");\n    fx_ensure_before_gl("glFramebufferTexture2D");\n    if (R_glFramebufferTexture2D) R_glFramebufferTexture2D(target, attachment, textarget, texture, level);\n    fx_check_gl_after("glFramebufferTexture2D", __builtin_return_address(0));\n}\n\nvoid glDisable(GLenum cap) {\n    resolve();\n    if (!R_glDisable) R_glDisable = (real_glDisable_t)sym_proc("glDisable");\n    fx_ensure_before_gl("glDisable");\n    if (R_glDisable) R_glDisable(cap);\n    fx_check_gl_after("glDisable", __builtin_return_address(0));\n}\n\nstatic void fx_ensure_before_gl(const char *what) {\n    ensure_current();\n    if (R_eglGetCurrentContext && R_eglGetCurrentContext() == EGL_NO_CONTEXT) {\n        fx_log("WARN %s called with NO current context on this thread", what);\n    }\n}\n\nstatic unsigned int fx_check_gl_after(const char *what, void *retaddr) {\n    if (!R_glGetErrorGL) return 0;\n    unsigned int e = R_glGetErrorGL();\n    if (e != 0) {\n        Dl_info dli;\n        const char *sym = "?";\n        if (dladdr(retaddr, &dli) && dli.dli_sname) sym = dli.dli_sname;\n        fx_log("SET-ERROR %s -> 0x%x (caller=%s)", what, e, sym);\n    }\n    return e;\n}\n\nunsigned int glGetError(void) {\n    resolve();\n    if (!R_glGetErrorGL) R_glGetErrorGL = (real_glGetError_t)sym_proc("glGetError");\n    if (!R_glGetErrorGL) return 0;\n    return R_glGetErrorGL();\n}\n\nvoid glGenTextures(GLsizei n, unsigned int *textures) {\n    resolve();\n    if (!R_glGenTextures) R_glGenTextures = (real_glGenTextures_t)sym_proc("glGenTextures");\n    fx_ensure_before_gl("glGenTextures");\n    if (R_glGenTextures) R_glGenTextures(n, textures);\n    fx_check_gl_after("glGenTextures", __builtin_return_address(0));\n}\n\nvoid glBindTexture(GLenum target, unsigned int texture) {\n    resolve();\n    if (!R_glBindTexture) R_glBindTexture = (real_glBindTexture_t)sym_proc("glBindTexture");\n    fx_ensure_before_gl("glBindTexture");\n    if (R_glBindTexture) R_glBindTexture(target, texture);\n    fx_check_gl_after("glBindTexture", __builtin_return_address(0));\n}\n\nvoid glTexImage2D(GLenum target, int level, int internalformat, GLsizei width, GLsizei height, int border, GLenum format, GLenum type, const void *pixels) {\n    resolve();\n    if (!R_glTexImage2D) R_glTexImage2D = (real_glTexImage2D_t)sym_proc("glTexImage2D");\n    fx_ensure_before_gl("glTexImage2D");\n    if (R_glTexImage2D) R_glTexImage2D(target, level, internalformat, width, height, border, format, type, pixels);\n    fx_check_gl_after("glTexImage2D", __builtin_return_address(0));\n}\n\nvoid glGenBuffers(GLsizei n, unsigned int *buffers) {\n    resolve();\n    if (!R_glGenBuffers) R_glGenBuffers = (real_glGenBuffers_t)sym_proc("glGenBuffers");\n    fx_ensure_before_gl("glGenBuffers");\n    if (R_glGenBuffers) R_glGenBuffers(n, buffers);\n    fx_check_gl_after("glGenBuffers", __builtin_return_address(0));\n}\n\nvoid glBufferData(GLenum target, long size, const void *data, GLenum usage) {\n    resolve();\n    if (!R_glBufferData) R_glBufferData = (real_glBufferData_t)sym_proc("glBufferData");\n    fx_ensure_before_gl("glBufferData");\n    if (R_glBufferData) R_glBufferData(target, size, data, usage);\n    fx_check_gl_after("glBufferData", __builtin_return_address(0));\n}\n\nvoid glClear(GLenum mask) {\n    resolve();\n    if (!R_glClear) R_glClear = (real_glClear_t)sym_proc("glClear");\n    fx_ensure_before_gl("glClear");\n    if (R_glClear) R_glClear(mask);\n    fx_check_gl_after("glClear", __builtin_return_address(0));\n}\n\n/* ------------------------------------------------------------------ */\n/* eglGetProcAddress: hand out wrappers for the shader-entry functions */\n/* ------------------------------------------------------------------ */\nstatic real_eglGetProcAddress_t R_eglGetProcAddress = NULL;\n\n__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *name) {\n    resolve();\n    if (!real_lib || !name) return NULL;\n    if (!R_eglGetProcAddress) R_eglGetProcAddress = (real_eglGetProcAddress_t)sym("eglGetProcAddress");\n    if (!R_eglGetProcAddress) return NULL;\n    __eglMustCastToProperFunctionPointerType r = R_eglGetProcAddress(name);\n    if (!r) return NULL;\n    if (!strcmp(name, "glCreateShader")) return (__eglMustCastToProperFunctionPointerType)&glCreateShader;\n    if (!strcmp(name, "glCreateProgram")) return (__eglMustCastToProperFunctionPointerType)&glCreateProgram;\n    if (!strcmp(name, "glGetError")) return (__eglMustCastToProperFunctionPointerType)&glGetError;\n    if (!strcmp(name, "glGenTextures")) return (__eglMustCastToProperFunctionPointerType)&glGenTextures;\n    if (!strcmp(name, "glBindTexture")) return (__eglMustCastToProperFunctionPointerType)&glBindTexture;\n    if (!strcmp(name, "glTexImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexImage2D;\n    if (!strcmp(name, "glGenBuffers")) return (__eglMustCastToProperFunctionPointerType)&glGenBuffers;\n    if (!strcmp(name, "glBufferData")) return (__eglMustCastToProperFunctionPointerType)&glBufferData;\n    if (!strcmp(name, "glClear")) return (__eglMustCastToProperFunctionPointerType)&glClear;\n    if (!strcmp(name, "glDrawArrays")) return (__eglMustCastToProperFunctionPointerType)&glDrawArrays;\n    if (!strcmp(name, "glDrawElements")) return (__eglMustCastToProperFunctionPointerType)&glDrawElements;\n    if (!strcmp(name, "glUseProgram")) return (__eglMustCastToProperFunctionPointerType)&glUseProgram;\n    if (!strcmp(name, "glBindVertexArray")) return (__eglMustCastToProperFunctionPointerType)&glBindVertexArray;\n    if (!strcmp(name, "glEnable")) return (__eglMustCastToProperFunctionPointerType)&glEnable;\n    if (!strcmp(name, "glDisable")) return (__eglMustCastToProperFunctionPointerType)&glDisable;\n    if (!strcmp(name, "glViewport")) return (__eglMustCastToProperFunctionPointerType)&glViewport;\n    if (!strcmp(name, "glTexSubImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexSubImage2D;\n    if (!strcmp(name, "glCompressedTexImage2D")) return (__eglMustCastToProperFunctionPointerType)&glCompressedTexImage2D;\n    if (!strcmp(name, "glTexStorage2D")) return (__eglMustCastToProperFunctionPointerType)&glTexStorage2D;\n    if (!strcmp(name, "glGenerateMipmap")) return (__eglMustCastToProperFunctionPointerType)&glGenerateMipmap;\n    if (!strcmp(name, "glPixelStorei")) return (__eglMustCastToProperFunctionPointerType)&glPixelStorei;\n    if (!strcmp(name, "glCreateTextures")) return (__eglMustCastToProperFunctionPointerType)&glCreateTextures;\n    if (!strcmp(name, "glTextureStorage2D")) return (__eglMustCastToProperFunctionPointerType)&glTextureStorage2D;\n    if (!strcmp(name, "glTextureSubImage2D")) return (__eglMustCastToProperFunctionPointerType)&glTextureSubImage2D;\n    if (!strcmp(name, "glBindFramebuffer")) return (__eglMustCastToProperFunctionPointerType)&glBindFramebuffer;\n    if (!strcmp(name, "glFramebufferTexture2D")) return (__eglMustCastToProperFunctionPointerType)&glFramebufferTexture2D;\n    return r;\n}\n\n/* ------------------------------------------------------------------ */\n/* eglMakeCurrent: forward + remember                                  */\n/* ------------------------------------------------------------------ */\nEGLBoolean eglMakeCurrent(EGLDisplay d, EGLSurface draw, EGLSurface read, EGLContext ctx) {\n    resolve();\n    if (!R_eglMakeCurrent) R_eglMakeCurrent = (real_eglMakeCurrent_t)sym("eglMakeCurrent");\n    if (!R_eglMakeCurrent) return EGL_FALSE;\n    EGLBoolean r = R_eglMakeCurrent(d, draw, read, ctx);\n    if (r == EGL_TRUE) {\n        pthread_mutex_lock(&g_lock);\n        if (ctx != EGL_NO_CONTEXT) {\n            EGLint w = -1, h = -1;\n            if (draw != EGL_NO_SURFACE && R_eglQuerySurface) {\n                R_eglQuerySurface(d, draw, EGL_WIDTH, &w);\n                R_eglQuerySurface(d, draw, EGL_HEIGHT, &h);\n            }\n            g_disp = d;\n            g_draw = draw;\n            g_read = read;\n            g_ctx = ctx;\n            g_surface_alive = (draw != EGL_NO_SURFACE);\n            g_ensured = 0; /* new binding -> allow one auto-rebind if needed */\n            fx_log("bind recorded d=%p draw=%p read=%p ctx=%p surfsize=%dx%d", d, draw, read, ctx, w, h);\n        } else {\n            fx_log("unbind (ctx=NULL) on d=%p — keeping last good binding for restore", d);\n        }\n        pthread_mutex_unlock(&g_lock);\n    } else {\n        fx_log("eglMakeCurrent FAILED d=%p draw=%p ctx=%p err=0x%x",\n               d, draw, ctx, R_eglGetError ? R_eglGetError() : 0);\n    }\n    return r;\n}\n\n/* ------------------------------------------------------------------ */\n/* other EGL calls: log + forward                                      */\n/* ------------------------------------------------------------------ */\nstatic void *(*R_eglGetDisplay)(EGLNativeDisplayType) = NULL;\nstatic void *(*R_eglGetPlatformDisplay)(EGLenum, void *, const EGLAttrib *) = NULL;\nstatic void *(*R_eglGetPlatformDisplayEXT)(EGLenum, void *, const EGLint *) = NULL;\n\nEGLDisplay eglGetDisplay(EGLNativeDisplayType display_id) {\n    resolve();\n    if (!R_eglGetDisplay) R_eglGetDisplay = (void *(*)(EGLNativeDisplayType))sym("eglGetDisplay");\n    return R_eglGetDisplay ? R_eglGetDisplay(display_id) : EGL_NO_DISPLAY;\n}\n\nEGLDisplay eglGetPlatformDisplay(EGLenum platform, void *native_display, const EGLAttrib *attrib_list) {\n    resolve();\n    if (!R_eglGetPlatformDisplay) R_eglGetPlatformDisplay = (void *(*)(EGLenum, void *, const EGLAttrib *))sym("eglGetPlatformDisplay");\n    return R_eglGetPlatformDisplay ? R_eglGetPlatformDisplay(platform, native_display, attrib_list) : EGL_NO_DISPLAY;\n}\n\nEGLDisplay eglGetPlatformDisplayEXT(EGLenum platform, void *native_display, const EGLint *attrib_list) {\n    resolve();\n    if (!R_eglGetPlatformDisplayEXT) R_eglGetPlatformDisplayEXT = (void *(*)(EGLenum, void *, const EGLint *))sym("eglGetPlatformDisplayEXT");\n    return R_eglGetPlatformDisplayEXT ? R_eglGetPlatformDisplayEXT(platform, native_display, attrib_list) : EGL_NO_DISPLAY;\n}\n\nEGLBoolean eglInitialize(EGLDisplay dpy, EGLint *major, EGLint *minor) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLint *, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint *, EGLint *))sym("eglInitialize");\n    return f ? f(dpy, major, minor) : EGL_FALSE;\n}\n\nEGLBoolean eglTerminate(EGLDisplay dpy) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay))sym("eglTerminate");\n    return f ? f(dpy) : EGL_FALSE;\n}\n\nconst char *eglQueryString(EGLDisplay dpy, EGLint name) {\n    resolve();\n    static const char *(*f)(EGLDisplay, EGLint) = NULL;\n    if (!f) f = (const char *(*)(EGLDisplay, EGLint))sym("eglQueryString");\n    return f ? f(dpy, name) : NULL;\n}\n\nEGLBoolean eglGetConfigs(EGLDisplay dpy, EGLConfig *configs, EGLint config_size, EGLint *num_config) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLConfig *, EGLint, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLConfig *, EGLint, EGLint *))sym("eglGetConfigs");\n    return f ? f(dpy, configs, config_size, num_config) : EGL_FALSE;\n}\n\nEGLBoolean eglChooseConfig(EGLDisplay dpy, const EGLint *attrib_list, EGLConfig *configs, EGLint config_size, EGLint *num_config) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *))sym("eglChooseConfig");\n    return f ? f(dpy, attrib_list, configs, config_size, num_config) : EGL_FALSE;\n}\n\nEGLBoolean eglGetConfigAttrib(EGLDisplay dpy, EGLConfig config, EGLint attribute, EGLint *value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLConfig, EGLint, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLConfig, EGLint, EGLint *))sym("eglGetConfigAttrib");\n    return f ? f(dpy, config, attribute, value) : EGL_FALSE;\n}\n\nstatic EGLSurface (*R_eglCreateWindowSurface)(EGLDisplay, EGLConfig, EGLNativeWindowType, const EGLint *) = NULL;\nstatic void *(*R_eglCreatePlatformWindowSurface)(EGLDisplay, EGLConfig, void *, const EGLAttrib *) = NULL;\nstatic void *(*R_eglCreatePlatformWindowSurfaceEXT)(EGLDisplay, EGLConfig, void *, const EGLint *) = NULL;\n\nEGLSurface eglCreateWindowSurface(EGLDisplay dpy, EGLConfig config, EGLNativeWindowType win, const EGLint *attrib_list) {\n    resolve();\n    if (!R_eglCreateWindowSurface) R_eglCreateWindowSurface = (EGLSurface (*)(EGLDisplay, EGLConfig, EGLNativeWindowType, const EGLint *))sym("eglCreateWindowSurface");\n    return R_eglCreateWindowSurface ? R_eglCreateWindowSurface(dpy, config, win, attrib_list) : EGL_NO_SURFACE;\n}\n\nEGLSurface eglCreatePlatformWindowSurface(EGLDisplay dpy, EGLConfig config, void *native_window, const EGLAttrib *attrib_list) {\n    resolve();\n    if (!R_eglCreatePlatformWindowSurface) R_eglCreatePlatformWindowSurface = (void *(*)(EGLDisplay, EGLConfig, void *, const EGLAttrib *))sym("eglCreatePlatformWindowSurface");\n    return R_eglCreatePlatformWindowSurface ? R_eglCreatePlatformWindowSurface(dpy, config, native_window, attrib_list) : EGL_NO_SURFACE;\n}\n\nEGLSurface eglCreatePlatformWindowSurfaceEXT(EGLDisplay dpy, EGLConfig config, void *native_window, const EGLint *attrib_list) {\n    resolve();\n    if (!R_eglCreatePlatformWindowSurfaceEXT) R_eglCreatePlatformWindowSurfaceEXT = (void *(*)(EGLDisplay, EGLConfig, void *, const EGLint *))sym("eglCreatePlatformWindowSurfaceEXT");\n    return R_eglCreatePlatformWindowSurfaceEXT ? R_eglCreatePlatformWindowSurfaceEXT(dpy, config, native_window, attrib_list) : EGL_NO_SURFACE;\n}\n\nEGLSurface eglCreatePbufferSurface(EGLDisplay dpy, EGLConfig config, const EGLint *attrib_list) {\n    resolve();\n    static EGLSurface (*f)(EGLDisplay, EGLConfig, const EGLint *) = NULL;\n    if (!f) f = (EGLSurface (*)(EGLDisplay, EGLConfig, const EGLint *))sym("eglCreatePbufferSurface");\n    return f ? f(dpy, config, attrib_list) : EGL_NO_SURFACE;\n}\n\nEGLBoolean eglDestroySurface(EGLDisplay dpy, EGLSurface surface) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface))sym("eglDestroySurface");\n    pthread_mutex_lock(&g_lock);\n    if (surface == g_draw || surface == g_read) {\n        g_surface_alive = 0;\n        fx_log("eglDestroySurface: recorded surface %p destroyed (d=%p)", surface, dpy);\n    }\n    pthread_mutex_unlock(&g_lock);\n    return f ? f(dpy, surface) : EGL_FALSE;\n}\n\nEGLBoolean eglQuerySurface(EGLDisplay dpy, EGLSurface surface, EGLint attribute, EGLint *value) {\n    resolve();\n    if (!R_eglQuerySurface) R_eglQuerySurface = (real_eglQuerySurface_t)sym("eglQuerySurface");\n    return R_eglQuerySurface ? R_eglQuerySurface(dpy, surface, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglSurfaceAttrib(EGLDisplay dpy, EGLSurface surface, EGLint attribute, EGLint value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint, EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint, EGLint))sym("eglSurfaceAttrib");\n    return f ? f(dpy, surface, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglBindTexImage(EGLDisplay dpy, EGLSurface surface, EGLint buffer) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint))sym("eglBindTexImage");\n    return f ? f(dpy, surface, buffer) : EGL_FALSE;\n}\n\nEGLBoolean eglReleaseTexImage(EGLDisplay dpy, EGLSurface surface, EGLint buffer) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLint))sym("eglReleaseTexImage");\n    return f ? f(dpy, surface, buffer) : EGL_FALSE;\n}\n\nEGLBoolean eglSwapInterval(EGLDisplay dpy, EGLint interval) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint))sym("eglSwapInterval");\n    return f ? f(dpy, interval) : EGL_FALSE;\n}\n\nEGLContext eglCreateContext(EGLDisplay dpy, EGLConfig config, EGLContext share_context, const EGLint *attrib_list) {\n    resolve();\n    static EGLContext (*f)(EGLDisplay, EGLConfig, EGLContext, const EGLint *) = NULL;\n    if (!f) f = (EGLContext (*)(EGLDisplay, EGLConfig, EGLContext, const EGLint *))sym("eglCreateContext");\n    EGLContext c = f ? f(dpy, config, share_context, attrib_list) : EGL_NO_CONTEXT;\n    if (c != EGL_NO_CONTEXT) {\n        pthread_mutex_lock(&g_lock);\n        /* remember first/primary context too, in case MakeCurrent record is missing */\n        g_disp = dpy;\n        g_ctx = c;\n        g_ensured = 0; /* new context -> allow one auto-rebind if needed */\n        pthread_mutex_unlock(&g_lock);\n        fx_log("eglCreateContext d=%p cfg=%p share=%p -> %p", dpy, config, share_context, c);\n    }\n    return c;\n}\n\nEGLBoolean eglDestroyContext(EGLDisplay dpy, EGLContext ctx) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLContext) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLContext))sym("eglDestroyContext");\n    pthread_mutex_lock(&g_lock);\n    if (ctx == g_ctx) {\n        g_ctx = EGL_NO_CONTEXT;\n        g_surface_alive = 0;\n        fx_log("eglDestroyContext: recorded ctx %p destroyed", ctx);\n    }\n    pthread_mutex_unlock(&g_lock);\n    return f ? f(dpy, ctx) : EGL_FALSE;\n}\n\nEGLContext eglGetCurrentContext(void) {\n    resolve();\n    if (!R_eglGetCurrentContext) R_eglGetCurrentContext = (real_eglGetCurrentContext_t)sym("eglGetCurrentContext");\n    return R_eglGetCurrentContext ? R_eglGetCurrentContext() : EGL_NO_CONTEXT;\n}\n\nEGLDisplay eglGetCurrentDisplay(void) {\n    resolve();\n    static EGLDisplay (*f)(void) = NULL;\n    if (!f) f = (EGLDisplay (*)(void))sym("eglGetCurrentDisplay");\n    return f ? f() : EGL_NO_DISPLAY;\n}\n\nEGLSurface eglGetCurrentSurface(EGLint readdraw) {\n    resolve();\n    static EGLSurface (*f)(EGLint) = NULL;\n    if (!f) f = (EGLSurface (*)(EGLint))sym("eglGetCurrentSurface");\n    return f ? f(readdraw) : EGL_NO_SURFACE;\n}\n\nEGLBoolean eglQueryContext(EGLDisplay dpy, EGLContext ctx, EGLint attribute, EGLint *value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLContext, EGLint, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLContext, EGLint, EGLint *))sym("eglQueryContext");\n    return f ? f(dpy, ctx, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglWaitClient(void) {\n    resolve();\n    static EGLBoolean (*f)(void) = NULL;\n    if (!f) f = (EGLBoolean (*)(void))sym("eglWaitClient");\n    return f ? f() : EGL_FALSE;\n}\n\nEGLBoolean eglWaitGL(void) {\n    resolve();\n    static EGLBoolean (*f)(void) = NULL;\n    if (!f) f = (EGLBoolean (*)(void))sym("eglWaitGL");\n    return f ? f() : EGL_FALSE;\n}\n\nEGLBoolean eglWaitNative(EGLint engine) {\n    resolve();\n    static EGLBoolean (*f)(EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLint))sym("eglWaitNative");\n    return f ? f(engine) : EGL_FALSE;\n}\n\nEGLBoolean eglSwapBuffers(EGLDisplay dpy, EGLSurface surface) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface))sym("eglSwapBuffers");\n    return f ? f(dpy, surface) : EGL_FALSE;\n}\n\nEGLBoolean eglCopyBuffers(EGLDisplay dpy, EGLSurface surface, EGLNativePixmapType target) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSurface, EGLNativePixmapType) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSurface, EGLNativePixmapType))sym("eglCopyBuffers");\n    return f ? f(dpy, surface, target) : EGL_FALSE;\n}\n\nEGLBoolean eglBindAPI(EGLenum api) {\n    resolve();\n    static EGLBoolean (*f)(EGLenum) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLenum))sym("eglBindAPI");\n    return f ? f(api) : EGL_FALSE;\n}\n\nEGLenum eglQueryAPI(void) {\n    resolve();\n    static EGLenum (*f)(void) = NULL;\n    if (!f) f = (EGLenum (*)(void))sym("eglQueryAPI");\n    return f ? f() : EGL_NONE;\n}\n\nEGLint eglGetError(void) {\n    resolve();\n    if (!R_eglGetError) R_eglGetError = (real_eglGetError_t)sym("eglGetError");\n    return R_eglGetError ? R_eglGetError() : 0;\n}\n\nEGLBoolean eglReleaseThread(void) {\n    resolve();\n    static EGLBoolean (*f)(void) = NULL;\n    if (!f) f = (EGLBoolean (*)(void))sym("eglReleaseThread");\n    fx_log("eglReleaseThread called");\n    return f ? f() : EGL_FALSE;\n}\n\nEGLBoolean eglGetVersion(EGLDisplay dpy, EGLint *major, EGLint *minor) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLint *, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint *, EGLint *))sym("eglGetVersion");\n    return f ? f(dpy, major, minor) : EGL_FALSE;\n}\n\nEGLBoolean eglWaitSync(EGLDisplay dpy, EGLSync sync, EGLint flags) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLint) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLint))sym("eglWaitSync");\n    return f ? f(dpy, sync, flags) : EGL_FALSE;\n}\n\nEGLBoolean eglQueryDisplayAttrib(EGLDisplay dpy, EGLint attribute, EGLAttrib *value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLint, EGLAttrib *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLint, EGLAttrib *))sym("eglQueryDisplayAttrib");\n    return f ? f(dpy, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglQueryDevicesEXT(EGLint max_devices, EGLDeviceEXT *devices, EGLint *num_devices) {\n    resolve();\n    static EGLBoolean (*f)(EGLint, EGLDeviceEXT *, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLint, EGLDeviceEXT *, EGLint *))sym("eglQueryDevicesEXT");\n    return f ? f(max_devices, devices, num_devices) : EGL_FALSE;\n}\n\nconst char *eglQueryDeviceStringEXT(EGLDeviceEXT device, EGLint name) {\n    resolve();\n    static const char *(*f)(EGLDeviceEXT, EGLint) = NULL;\n    if (!f) f = (const char *(*)(EGLDeviceEXT, EGLint))sym("eglQueryDeviceStringEXT");\n    return f ? f(device, name) : NULL;\n}\n\nEGLImage eglCreateImage(EGLDisplay dpy, EGLContext ctx, EGLenum target, EGLClientBuffer buffer, const EGLAttrib *attrib_list) {\n    resolve();\n    static EGLImage (*f)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLAttrib *) = NULL;\n    if (!f) f = (EGLImage (*)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLAttrib *))sym("eglCreateImage");\n    return f ? f(dpy, ctx, target, buffer, attrib_list) : EGL_NO_IMAGE;\n}\n\nEGLImage eglCreateImageKHR(EGLDisplay dpy, EGLContext ctx, EGLenum target, EGLClientBuffer buffer, const EGLint *attrib_list) {\n    resolve();\n    static EGLImage (*f)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *) = NULL;\n    if (!f) f = (EGLImage (*)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *))sym("eglCreateImageKHR");\n    return f ? f(dpy, ctx, target, buffer, attrib_list) : EGL_NO_IMAGE;\n}\n\nEGLBoolean eglDestroyImage(EGLDisplay dpy, EGLImage image) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLImage) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLImage))sym("eglDestroyImage");\n    return f ? f(dpy, image) : EGL_FALSE;\n}\n\nEGLBoolean eglDestroyImageKHR(EGLDisplay dpy, EGLImageKHR image) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLImageKHR) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLImageKHR))sym("eglDestroyImageKHR");\n    return f ? f(dpy, image) : EGL_FALSE;\n}\n\nEGLSync eglCreateSync(EGLDisplay dpy, EGLenum type, const EGLAttrib *attrib_list) {\n    resolve();\n    static EGLSync (*f)(EGLDisplay, EGLenum, const EGLAttrib *) = NULL;\n    if (!f) f = (EGLSync (*)(EGLDisplay, EGLenum, const EGLAttrib *))sym("eglCreateSync");\n    return f ? f(dpy, type, attrib_list) : EGL_NO_SYNC;\n}\n\nEGLSync eglCreateSyncKHR(EGLDisplay dpy, EGLenum type, const EGLint *attrib_list) {\n    resolve();\n    static EGLSync (*f)(EGLDisplay, EGLenum, const EGLint *) = NULL;\n    if (!f) f = (EGLSync (*)(EGLDisplay, EGLenum, const EGLint *))sym("eglCreateSyncKHR");\n    return f ? f(dpy, type, attrib_list) : EGL_NO_SYNC;\n}\n\nEGLBoolean eglDestroySync(EGLDisplay dpy, EGLSync sync) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSync) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync))sym("eglDestroySync");\n    return f ? f(dpy, sync) : EGL_FALSE;\n}\n\nEGLBoolean eglDestroySyncKHR(EGLDisplay dpy, EGLSyncKHR sync) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR))sym("eglDestroySyncKHR");\n    return f ? f(dpy, sync) : EGL_FALSE;\n}\n\nEGLint eglClientWaitSync(EGLDisplay dpy, EGLSync sync, EGLint flags, EGLTime timeout) {\n    resolve();\n    static EGLint (*f)(EGLDisplay, EGLSync, EGLint, EGLTime) = NULL;\n    if (!f) f = (EGLint (*)(EGLDisplay, EGLSync, EGLint, EGLTime))sym("eglClientWaitSync");\n    return f ? f(dpy, sync, flags, timeout) : EGL_FALSE;\n}\n\nEGLint eglClientWaitSyncKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLint flags, EGLTimeKHR timeout) {\n    resolve();\n    static EGLint (*f)(EGLDisplay, EGLSyncKHR, EGLint, EGLTimeKHR) = NULL;\n    if (!f) f = (EGLint (*)(EGLDisplay, EGLSyncKHR, EGLint, EGLTimeKHR))sym("eglClientWaitSyncKHR");\n    return f ? f(dpy, sync, flags, timeout) : EGL_FALSE;\n}\n\nEGLBoolean eglGetSyncAttrib(EGLDisplay dpy, EGLSync sync, EGLint attribute, EGLAttrib *value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLint, EGLAttrib *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLint, EGLAttrib *))sym("eglGetSyncAttrib");\n    return f ? f(dpy, sync, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglGetSyncAttribKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLint attribute, EGLint *value) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR, EGLint, EGLint *) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR, EGLint, EGLint *))sym("eglGetSyncAttribKHR");\n    return f ? f(dpy, sync, attribute, value) : EGL_FALSE;\n}\n\nEGLBoolean eglSignalSync(EGLDisplay dpy, EGLSync sync, EGLenum mode) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSync, EGLenum) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSync, EGLenum))sym("eglSignalSync");\n    return f ? f(dpy, sync, mode) : EGL_FALSE;\n}\n\nEGLBoolean eglSignalSyncKHR(EGLDisplay dpy, EGLSyncKHR sync, EGLenum mode) {\n    resolve();\n    static EGLBoolean (*f)(EGLDisplay, EGLSyncKHR, EGLenum) = NULL;\n    if (!f) f = (EGLBoolean (*)(EGLDisplay, EGLSyncKHR, EGLenum))sym("eglSignalSyncKHR");\n    return f ? f(dpy, sync, mode) : EGL_FALSE;\n}\n\n/* ------------------------------------------------------------------ */\n/* constructor: log that we are loaded                                 */\n/* ------------------------------------------------------------------ */\n__attribute__((constructor)) static void fx_init(void) {\n    char buf[128];\n    snprintf(buf, sizeof(buf), "eglfix shim loaded (pid=%d)", (int)getpid());\n    fx_log("%s", buf);\n    resolve();\n}\n'
EXPORT_MAP = '/*\n * export.map — restrict exported dynamic symbols to the EGL/GL API only.\n *\n * Everything else (internal helpers like fx_log, fx_ensure_before_gl, etc.)\n * becomes local, so this interposer can never accidentally shadow libc/libdl\n * symbols in any process that dlopens it as libEGL.so.1.\n */\n{\n    global:\n        egl*;\n        gl*;\n    local:\n        *;\n};\n'

# README written next to the installed shim so the fix dir is self-documenting.
SHIM_README = """# Factorio 2.1.14 native-Wayland fix (libEGL shim)

Managed by fix_run_on_wayland.py (regenerated on every run).

The fix: an EGL interposer (libEGL.so.1) loaded via LD_PRELOAD that forwards
all EGL calls to the real glvnd libEGL and re-binds the remembered EGL context
on the calling thread right before the game's first glCreateShader -- this
fixes the native-Wayland-only crash:

    Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.

Root cause: on the Wayland/EGL path Factorio loses EGL context currency on the
main thread between GL init and first shader compile, so glCreateShader returns
0 and the game aborts. X11 works; native Wayland does not.

Files: libEGL.so.1 (built), eglfix.c + export.map (sources), README.md.

Wiring: <game>/local.config gets ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"
(the `env` prefix is REQUIRED -- the jc141 launcher word-splits $ENV into the
command array and bwrap only execs real binaries; $HOME expands at source time
and resolves in both sandboxed and unsandboxed launches).

Reset: run `fix_run_on_wayland.py --reset` to remove this dir and the config line.
Diagnostics: the shim logs to /tmp/eglfix.log.
"""

# Small GL/EGL smoke test: creates a surfaceless context, then simulates the
# Factorio bug (unbind, then glCreateShader with no current context).
# Without the shim this must FAIL (glCreateShader -> 0, the crash condition);
# with the shim preloaded it must PASS (shim re-binds and the shader is made).
SMOKE_TEST_C = r"""
#define EGL_EGLEXT_PROTOTYPES 1
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <stdio.h>

#ifndef EGL_PLATFORM_SURFACELESS_MESA
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#endif

typedef unsigned int GLenum;
typedef unsigned int GLuint;
typedef const unsigned char *(*PFN_glGetString)(GLenum);
typedef void (*PFN_glClear)(GLenum);
typedef GLuint (*PFN_glCreateShader)(GLenum);
typedef EGLDisplay (*PFN_eglGetPlatformDisplayEXT)(EGLenum, void *, const EGLint *);

int main(void) {
    /* Prefer the surfaceless platform (headless-safe); resolve the EXT
     * entry point via eglGetProcAddress because glvnd does not export it. */
    EGLDisplay dpy = EGL_NO_DISPLAY;
    PFN_eglGetPlatformDisplayEXT pGetPlatformDisplayEXT =
        (PFN_eglGetPlatformDisplayEXT)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (pGetPlatformDisplayEXT) {
        dpy = pGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    }
    if (dpy == EGL_NO_DISPLAY) dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (dpy == EGL_NO_DISPLAY) { printf("FAIL: no EGL display\n"); return 1; }

    EGLint major = 0, minor = 0;
    if (!eglInitialize(dpy, &major, &minor)) { printf("FAIL: eglInitialize\n"); return 1; }
    if (!eglBindAPI(EGL_OPENGL_API)) { printf("FAIL: eglBindAPI\n"); return 1; }

    EGLint ca[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
                    EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    if (!eglChooseConfig(dpy, ca, &cfg, 1, &n) || n < 1) { printf("FAIL: eglChooseConfig\n"); return 1; }

    EGLContext ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, NULL);
    if (ctx == EGL_NO_CONTEXT) { printf("FAIL: eglCreateContext\n"); return 1; }
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { printf("FAIL: eglMakeCurrent\n"); return 1; }

    PFN_glGetString glGetString = (PFN_glGetString)eglGetProcAddress("glGetString");
    PFN_glClear glClear = (PFN_glClear)eglGetProcAddress("glClear");
    PFN_glCreateShader glCreateShader = (PFN_glCreateShader)eglGetProcAddress("glCreateShader");

    if (glGetString) printf("GL_VERSION=%s\n", (const char *)glGetString(0x1F02));
    if (glClear) glClear(0x4000);
    printf("pre-unbind glGetError=0x%x\n", (unsigned)eglGetError());

    /* Simulate the Factorio bug: lose context currency, then compile a shader. */
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT)) { printf("FAIL: unbind\n"); return 1; }
    printf("eglGetCurrentContext after unbind=%p (0 == lost, exactly like Factorio)\n",
           (void *)eglGetCurrentContext());

    GLuint shader = glCreateShader ? glCreateShader(0x8B31) : 0; /* GL_VERTEX_SHADER */
    printf("glCreateShader after unbind -> %u\n", shader);
    if (shader != 0) {
        printf("PASS: shader created without current context (shim re-bound it)\n");
        return 0;
    }
    printf("FAIL: glCreateShader returned 0 -- this IS the Factorio crash condition\n");
    return 1;
}
"""

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
PACKAGES_FIX = ["gcc", "libglvnd", "sdl3", "fuse-overlayfs", "bubblewrap", "python-rich"]
PACKAGES_TEST_TOOLS = ["wtype", "grim", "imagemagick"]

SHIM_SUBDIR = os.path.join(".factorio", "wayland_fix")
ENV_LINE = 'ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"'
MARKER = "# Factorio native-Wayland shader fix (managed by fix_run_on_wayland.py)"
CREATED_MARKER = "# created by fix_run_on_wayland.py because the repack had no local.config"


def log(msg, level="*"):
    print("[%s] %s" % (level, msg), flush=True)


def rich_table(title, rows):
    """Pretty table if rich is available, else aligned plain text."""
    if RICH:
        t = Table(title=title)
        t.add_column("Item", style="cyan", no_wrap=True)
        t.add_column("Status", style="bold")
        t.add_column("Detail", style="dim")
        for item, status, detail in rows:
            t.add_row(item, status, detail)
        Console().print(t)
    else:
        print("== %s ==" % title)
        for item, status, detail in rows:
            print("  %-22s %-12s %s" % (item, status, detail))


def run(cmd, timeout=120, env=None, input_data=None):
    """Run a command; return CompletedProcess. Timeouts abort loudly.

    NOTE: do not pass input=None explicitly -- CPython then sets the child's
    stdin to /dev/null, which breaks sudo -S reading a piped password.
    """
    kwargs = dict(capture_output=True, text=True, timeout=timeout)
    if env is not None:
        kwargs["env"] = env
    if input_data is not None:
        kwargs["input"] = input_data
    try:
        return subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        raise SystemExit("COMMAND TIMED OUT after %ss: %s\n%s%s" % (timeout, " ".join(cmd), out, err))


def sudo_cmd():
    """Return a sudo command list. Uses -n if passwordless sudo works,
    otherwise plain sudo (interactive). Supports SUDO_STDIN=1 for -S."""
    if not shutil.which("sudo"):
        raise SystemExit("ERROR: sudo not found -- install it with: pacman -S sudo "
                         "(or run this script as root / with a root-capable user)")
    probe = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    if probe.returncode == 0:
        return ["sudo", "-n"]
    if os.environ.get("SUDO_STDIN") == "1":
        return ["sudo", "-S"]
    return ["sudo"]


def run_root(cmd, input_data=None, timeout=120):
    """Run a command with root privileges (auto-elevate)."""
    return run(sudo_cmd() + cmd, input_data=input_data, timeout=timeout)


def find_game_dir(guess):
    """Locate the Factorio jc141 repack directory."""
    candidates = []
    if guess:
        candidates.append(os.path.abspath(os.path.expanduser(guess)))
    env_dir = os.environ.get("FACTORIO_DIR")
    if env_dir:
        candidates.append(os.path.abspath(os.path.expanduser(env_dir)))
    home = os.path.expanduser("~")
    candidates += [
        "/mnt/zram1/Factorio_2.1.14",
        os.path.join(home, "Factorio_2.1.14"),
        os.path.join(home, "Downloads", "Factorio-jc141"),
        os.path.join(home, "Games", "Factorio_2.1.14"),
        os.path.join(home, "Games", "jc141", "Factorio_2.1.14"),
        os.path.join(os.getcwd(), "Factorio_2.1.14"),
        os.getcwd(),
    ]
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        # local.config is NOT required -- fresh jc141 repacks may not ship one
        # (the launcher auto-generates it, and we create+wiring it ourselves).
        if (os.path.isfile(os.path.join(c, "start.n.sh"))
                or os.path.isfile(os.path.join(c, "space.age.sh"))):
            return c
    return None


def distro_supports_pacman():
    return shutil.which("pacman") is not None


def package_installed(pkg):
    r = run(["pacman", "-Q", pkg])
    return r.returncode == 0


def install_packages(pkgs, skip):
    if skip:
        log("--skip-packages: skipping package installation")
        return
    missing = [p for p in pkgs if not package_installed(p)]
    if not missing:
        log("all required packages already installed: %s" % ", ".join(pkgs))
        return
    log("installing packages: %s (may prompt for sudo password)" % ", ".join(missing))
    # Fresh-install pacman must sync the repo DB and download packages; that
    # can take minutes, so give it a generous timeout.
    r = run_root(["pacman", "-S", "--needed", "--noconfirm"] + missing, timeout=600)
    if r.returncode != 0:
        raise SystemExit("ERROR: package install failed:\n%s%s" % (r.stdout, r.stderr))
    for p in missing:
        if not package_installed(p):
            raise SystemExit("ERROR: package '%s' still not installed after pacman -S" % p)
    log("packages installed successfully")


def check_prereqs():
    if not shutil.which("gcc"):
        raise SystemExit("ERROR: gcc not found. Run without --skip-packages, or: sudo pacman -S gcc")
    if not os.path.isfile("/usr/include/EGL/egl.h"):
        raise SystemExit("ERROR: EGL headers missing. Run without --skip-packages, or: sudo pacman -S libglvnd")
    if not os.path.isfile("/usr/lib/libEGL.so.1"):
        raise SystemExit("ERROR: runtime libEGL.so.1 missing. Run without --skip-packages, or: sudo pacman -S libglvnd")


def write_if_changed(path, content):
    """Write content only if it differs; returns True if written."""
    try:
        with open(path) as f:
            if f.read() == content:
                return False
    except OSError:
        pass
    with open(path, "w") as f:
        f.write(content)
    return True


# --------------------------------------------------------------------------
# Shim locations
# --------------------------------------------------------------------------
def jc141_sandbox_home():
    """Sandbox HOME used by the jc141 launcher: JC_DIRECTORY/native-docs
    (default $HOME/Games/jc141/native-docs). Returns None if unparseable."""
    home = os.path.expanduser("~")
    jc = os.path.join(home, "Games", "jc141")
    rc = os.path.join(home, ".jc141rc")
    if os.path.isfile(rc):
        try:
            with open(rc) as f:
                for ln in f:
                    m = re.match(r"^\s*JC_DIRECTORY\s*=\s*(.*)$", ln)
                    if m and not ln.strip().startswith("#"):
                        val = os.path.expandvars(m.group(1).strip().strip('"').strip("'"))
                        if val:
                            jc = val
                        break
        except OSError:
            pass
    return os.path.join(jc, "native-docs")


def shim_locations():
    """Return (primary_dir, sandbox_dir, shim_path). The shim_path is the
    $HOME-relative form used in local.config (resolves in both launch modes)."""
    home = os.path.expanduser("~")
    primary_dir = os.path.join(home, SHIM_SUBDIR)
    sandbox = jc141_sandbox_home()
    sandbox_dir = os.path.join(sandbox, SHIM_SUBDIR) if sandbox else None
    return primary_dir, sandbox_dir, os.path.join(primary_dir, "libEGL.so.1")


def build_shim(primary_dir, force=False):
    """Write embedded sources and compile libEGL.so.1 in primary_dir.
    Returns (shim_path, rebuilt_bool)."""
    os.makedirs(primary_dir, exist_ok=True)
    src_path = os.path.join(primary_dir, "eglfix.c")
    map_path = os.path.join(primary_dir, "export.map")
    shim_path = os.path.join(primary_dir, "libEGL.so.1")

    src_written = write_if_changed(src_path, EGLFIX_C)
    write_if_changed(map_path, EXPORT_MAP)
    write_if_changed(os.path.join(primary_dir, "README.md"), SHIM_README)

    need_build = force or not os.path.isfile(shim_path) or src_written
    if not need_build:
        try:
            if os.path.getmtime(src_path) > os.path.getmtime(shim_path):
                need_build = True
        except OSError:
            need_build = True
    if not need_build:
        log("shim already built and up to date: %s" % shim_path)
        return shim_path, False

    log("compiling EGL interposer...")
    tmp = tempfile.mkdtemp(prefix="eglfix_build_")
    try:
        out_tmp = os.path.join(tmp, "libEGL.so.1")
        cmd = ["gcc", "-shared", "-fPIC", "-O2", "-Wall",
               "-o", out_tmp, src_path, "-ldl", "-pthread",
               "-Wl,-soname,libEGL.so.1", "-Wl,--version-script=%s" % map_path]
        r = run(cmd)
        if r.returncode != 0:
            raise SystemExit("ERROR: gcc failed:\n%s%s" % (r.stdout, r.stderr))
        shutil.copyfile(out_tmp, shim_path)
        os.chmod(shim_path, 0o755)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if shutil.which("readelf"):
        r = run(["readelf", "-d", shim_path])
        if "Library soname: [libEGL.so.1]" not in r.stdout:
            raise SystemExit("ERROR: built shim has wrong SONAME (expected libEGL.so.1)")
    log("built and verified: %s" % shim_path)
    return shim_path, True


def install_shims(primary_dir, sandbox_dir, force=False):
    """Build the shim and mirror it into the sandbox location. Returns shim_path."""
    shim_path, _ = build_shim(primary_dir, force=force)
    if sandbox_dir and os.path.abspath(sandbox_dir) != os.path.abspath(primary_dir):
        os.makedirs(sandbox_dir, exist_ok=True)
        dst = os.path.join(sandbox_dir, "libEGL.so.1")
        if not os.path.isfile(dst) or open(dst, "rb").read() != open(shim_path, "rb").read():
            shutil.copyfile(shim_path, dst)
            os.chmod(dst, 0o755)
            log("mirrored shim into sandbox home: %s" % dst)
        else:
            log("sandbox copy up to date: %s" % dst)
    return shim_path


def remove_legacy_game_dir_shim(game_dir):
    """Remove the old <game>/eglfix/ dir (pre-persistent-location layout)."""
    legacy = os.path.join(game_dir, "eglfix")
    if os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)
        log("removed legacy in-game eglfix/ dir: %s" % legacy)


def wire_config(cfg_path):
    """Idempotently set the ENV= fix line in local.config.

    Creates a minimal local.config if the repack didn't ship one (the jc141
    launcher would auto-generate its own all-commented copy otherwise)."""
    if not os.path.isfile(cfg_path):
        header = ("# this file is used by jc141 start scripts to specify game-specific settings\n"
                  + CREATED_MARKER + "\n")
        with open(cfg_path, "w") as f:
            f.write(header + ENV_LINE + "\n")
        log("created %s (repack had none) and set the ENV= fix line" % cfg_path)
        return True
    with open(cfg_path) as f:
        lines = f.readlines()

    # Drop stale commented-out ENV lines and old-format fix markers left by
    # earlier versions (e.g. '#ENV=REMOVED_BY_TEST', 'see eglfix/README.md')
    # so the config stays tidy.
    lines = [ln for ln in lines
             if not re.match(r"^\s*#\s*ENV=", ln)
             and "Factorio native-Wayland shader fix" not in ln]

    idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^\s*ENV=", ln):
            idx = i
            break

    changed = False
    if idx is not None:
        if lines[idx].strip() == ENV_LINE:
            log("launcher config already wired correctly")
            return False
        lines[idx] = ENV_LINE + "\n"
        changed = True
        log("updated existing ENV= line in %s" % cfg_path)
    else:
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.strip() and not ln.startswith("#"):
                insert_at = i
                break
            insert_at = i + 1
        lines.insert(insert_at, ENV_LINE + "\n")
        lines.insert(insert_at, MARKER + "\n")
        changed = True
        log("added ENV= line to %s" % cfg_path)

    if changed:
        # Preserve the pristine config: only write the backup once.
        if not os.path.isfile(cfg_path + ".bak"):
            shutil.copyfile(cfg_path, cfg_path + ".bak")
        with open(cfg_path, "w") as f:
            f.writelines(lines)
    return changed


def uninstall_fix(game_dir):
    """Remove everything the installer generated (--reset)."""
    primary_dir, sandbox_dir, _ = shim_locations()
    removed = []
    for d in (primary_dir, sandbox_dir):
        if d and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    legacy = os.path.join(game_dir, "eglfix") if game_dir else None
    if legacy and os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)
        removed.append(legacy)

    cfg_path = os.path.join(game_dir, "local.config") if game_dir else None
    if cfg_path and os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
        if CREATED_MARKER in content:
            os.remove(cfg_path)
            removed.append(cfg_path)
            bak = cfg_path + ".bak"
            if os.path.isfile(bak):
                os.remove(bak)
                removed.append(bak)
        else:
            with open(cfg_path) as f:
                lines = f.readlines()
            kept = [ln for ln in lines
                    if not re.match(r"^\s*ENV=", ln)
                    and "Factorio native-Wayland shader fix" not in ln]
            if kept != lines:
                with open(cfg_path, "w") as f:
                    f.writelines(kept)
                removed.append("%s (ENV line removed)" % cfg_path)
            bak = cfg_path + ".bak"
            if os.path.isfile(bak) and "LD_PRELOAD" in open(bak).read():
                os.remove(bak)
                removed.append(bak)
    for r_ in removed:
        log("removed: %s" % r_)
    if not removed:
        log("nothing to remove -- fix was not installed")
    return removed


# --------------------------------------------------------------------------
# Launcher helpers
# --------------------------------------------------------------------------
def ensure_executables(game_dir):
    """Fresh jc141 extractions frequently lose exec bits (launchers and the
    DwarFS binary come out rw-r--r--), which breaks ./start.n.sh with
    'Permission denied'. Restore them so the game can actually be launched."""
    fixed = []
    for rel in ("start.n.sh", "space.age.sh", "actions.sh",
                os.path.join("files", "dwarfs-binary")):
        p = os.path.join(game_dir, rel)
        if os.path.isfile(p) and not os.access(p, os.X_OK):
            try:
                os.chmod(p, 0o755)
                fixed.append(rel)
            except OSError as e:
                log("could not chmod %s: %s" % (p, e), "!")
    if fixed:
        log("restored exec bit on: %s" % ", ".join(fixed))
    return fixed


def check_launcher_compat(game_dir):
    """The jc141 ENV feature needs start.n.sh to use $ENV word-split."""
    for launcher in ("start.n.sh", "space.age.sh"):
        p = os.path.join(game_dir, launcher)
        if os.path.isfile(p):
            with open(p) as f:
                content = f.read()
            if "ENV" not in content:
                log("WARNING: %s does not reference $ENV -- the fix may not auto-apply; "
                    "manually run: LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1 ./runtime/run.sh ./factorio"
                    % launcher, "!")


def run_smoke_test(shim_path):
    """Compile the smoke test and run it twice: baseline (expect FAIL) and with
    the shim (expect PASS). Returns True if the shim run passed."""
    tmp = tempfile.mkdtemp(prefix="eglfix_smoke_")
    try:
        test_c = os.path.join(tmp, "eglfix_test.c")
        test_bin = os.path.join(tmp, "eglfix_test")
        with open(test_c, "w") as f:
            f.write(SMOKE_TEST_C)
        r = run(["gcc", "-O2", "-o", test_bin, test_c, "-lEGL", "-ldl"])
        if r.returncode != 0:
            raise SystemExit("ERROR: smoke test compile failed:\n%s%s" % (r.stdout, r.stderr))

        base = run([test_bin])
        base_failed = base.returncode != 0 or "PASS" not in base.stdout
        log("baseline (no shim): %s"
            % ("reproduced crash condition (expected)" if base_failed else "UNEXPECTEDLY passed"))

        os.environ.pop("LD_PRELOAD", None)
        run(["rm", "-f", "/tmp/eglfix.log"])
        env = dict(os.environ)
        env["LD_PRELOAD"] = shim_path
        shim_run = run([test_bin], env=env)
        print("    " + shim_run.stdout.replace("\n", "\n    ").rstrip())
        if shim_run.returncode == 0 and "PASS" in shim_run.stdout:
            log("SMOKE TEST PASSED: shim re-binds the context (fix verified)")
            return True
        log("SMOKE TEST FAILED with the shim loaded. Check /tmp/eglfix.log:", "!")
        if os.path.isfile("/tmp/eglfix.log"):
            with open("/tmp/eglfix.log") as f:
                for ln in f.read().splitlines():
                    print("    " + ln)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def kill_game_procs(proc):
    """Kill the whole process tree we launched for verification. The launcher
    is spawned as a session leader, so killing its process group covers the
    start.n.sh script and the bwrap wrapper. NOTE: the jc141 launcher invokes
    bwrap with --new-session, so the sandboxed game lives in its OWN session
    outside our process group -- the pkill -x factorio fallback below is what
    actually kills the game. Do not "simplify" it away."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        subprocess.run(["pkill", "-9", "-x", "factorio"], capture_output=True)
    except Exception:
        pass
    time.sleep(2)


def verify_game_launch(game_dir, max_wait=75):
    """Launch the game via start.n.sh (shim applies automatically through the
    wired local.config) and check the log for either 'Factorio initialised'
    (PASS) or 'Failed to create shader' (FAIL). Requires a live Wayland/X11
    session; returns True/False, or None if the check can't run here."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        log("no display session (WAYLAND_DISPLAY/DISPLAY unset) -- "
            "skipping game launch check", "!")
        return None
    if subprocess.run(["pgrep", "-x", "factorio"],
                      capture_output=True).returncode == 0:
        log("a Factorio process is already running -- skipping game launch check", "!")
        return None
    launcher = os.path.join(game_dir, "start.n.sh")
    if not os.path.isfile(launcher):
        log("start.n.sh not found -- skipping game launch check", "!")
        return None
    if not os.access(launcher, os.X_OK):
        log("start.n.sh is not executable (chmod +x needed) -- skipping game launch check", "!")
        return None

    log("launching game via %s (up to %ss)..." % (launcher, max_wait))
    log_path = os.path.join(tempfile.gettempdir(), "eglfix_verify.log")
    try:
        os.remove(log_path)
    except OSError:
        pass
    with open(log_path, "w") as out:
        proc = subprocess.Popen([launcher], stdout=out, stderr=subprocess.STDOUT,
                                cwd=game_dir, start_new_session=True)
    ok = None
    try:
        start = time.time()
        while time.time() - start < max_wait:
            if proc.poll() is not None:
                log("launcher exited early (rc=%s)" % proc.returncode, "!")
                break
            time.sleep(2)
            try:
                with open(log_path) as f:
                    content = f.read()
            except OSError:
                continue
            if "Failed to create shader" in content:
                ok = False
                break
            if "Factorio initialised" in content:
                ok = True
                break
    finally:
        kill_game_procs(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    if ok is True:
        log("GAME LAUNCH CHECK PASSED: reached 'Factorio initialised' on native Wayland")
        return True
    if ok is False:
        log("GAME LAUNCH CHECK FAILED: 'Failed to create shader' appeared in the log", "!")
        return False
    log("GAME LAUNCH CHECK INCONCLUSIVE: no 'Factorio initialised' within %ss and no "
        "shader error either -- treat as unverified, not broken. Log: %s"
        % (max_wait, log_path), "!")
    return None


# --------------------------------------------------------------------------
# Inspection / troubleshooting
# --------------------------------------------------------------------------
def gather_state(game_dir):
    """Collect a list of (item, status, detail) rows for --check/--troubleshoot."""
    primary_dir, sandbox_dir, shim_path = shim_locations()
    rows = []

    def add(item, ok, detail=""):
        rows.append((item, "OK" if ok else "PROBLEM", detail))
        return ok

    add("Arch/pacman", distro_supports_pacman(), "pacman found" if distro_supports_pacman() else "pacman missing")
    for pkg in PACKAGES_FIX:
        add("package %s" % pkg, package_installed(pkg))
    add("gcc", shutil.which("gcc") is not None, shutil.which("gcc") or "missing")
    add("EGL headers", os.path.isfile("/usr/include/EGL/egl.h"), "/usr/include/EGL/egl.h")
    add("real libEGL", os.path.isfile("/usr/lib/libEGL.so.1"), "/usr/lib/libEGL.so.1")

    if game_dir:
        add("game dir", True, game_dir)
        add("start.n.sh exec", os.access(os.path.join(game_dir, "start.n.sh"), os.X_OK))
        cfg = os.path.join(game_dir, "local.config")
        wired = os.path.isfile(cfg) and ENV_LINE in open(cfg).read()
        add("launcher wired", wired, "ENV line in local.config" if wired else "ENV line missing")

    shim_ok = os.path.isfile(shim_path)
    add("shim (primary)", shim_ok, shim_path if shim_ok else "~/.factorio/wayland_fix/libEGL.so.1 missing")
    if sandbox_dir:
        s_ok = os.path.isfile(os.path.join(sandbox_dir, "libEGL.so.1"))
        add("shim (sandbox)", s_ok, os.path.join(sandbox_dir, "libEGL.so.1"))

    game_proc = subprocess.run(["pgrep", "-x", "factorio"], capture_output=True).returncode == 0
    rows.append(("game running", "RUNNING" if game_proc else "idle",
                 "a Factorio process is running" if game_proc else "not running (normal)"))
    return rows, primary_dir, sandbox_dir, shim_path


def inspect_state(game_dir):
    """--check: report state, return problem count."""
    rows, _, _, _ = gather_state(game_dir)
    rich_table("Factorio Wayland-fix inspection", rows)
    problems = sum(1 for _, status, _ in rows if status == "PROBLEM")
    print("\n  problems found: %d" % problems)
    return problems


def run_troubleshoot(game_dir):
    """--troubleshoot: full read-only diagnostic report."""
    print()
    rows, primary_dir, sandbox_dir, shim_path = gather_state(game_dir)
    rich_table("System & fix state", rows)

    print("\n-- Smoke test --")
    if shutil.which("gcc") and os.path.isfile(shim_path):
        try:
            run_smoke_test(shim_path)
        except SystemExit as e:
            print("  " + str(e))
    else:
        print("  skipped (gcc or shim missing)")

    print("\n-- Shim diagnostics (/tmp/eglfix.log) --")
    if os.path.isfile("/tmp/eglfix.log"):
        with open("/tmp/eglfix.log") as f:
            for ln in f.read().splitlines()[-12:]:
                print("  " + ln)
    else:
        print("  (no log yet -- the shim writes here when the game runs)")

    print("\n-- Game logs --")
    for logpath in (os.path.join(jc141_sandbox_home(), ".factorio", "factorio-current.log"),
                    os.path.join(os.path.expanduser("~"), ".factorio", "factorio-current.log")):
        if os.path.isfile(logpath):
            print("  %s:" % logpath)
            with open(logpath) as f:
                lines = f.read().splitlines()
            interesting = [ln for ln in lines if re.search(r"Error|error|Shader|initialised|Video driver|Failed", ln)]
            for ln in interesting[-8:]:
                print("    " + ln)
        else:
            print("  %s: (absent)" % logpath)

    print("\n-- Suggested fixes --")
    hints = []
    if game_dir and not os.access(os.path.join(game_dir, "start.n.sh"), os.X_OK):
        hints.append("launcher exec bits missing -> run the installer (no flags)")
    if not os.path.isfile(shim_path):
        hints.append("shim missing -> run the installer (no flags)")
    if game_dir:
        cfg = os.path.join(game_dir, "local.config")
        if not (os.path.isfile(cfg) and ENV_LINE in open(cfg).read()):
            hints.append("launcher not wired -> run the installer (no flags)")
    if not hints:
        hints.append("everything looks good -- if the game still crashes, run `--reset` then re-install")
    for h in hints:
        print("  * " + h)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Install/repair the Factorio native-Wayland shader fix.")
    ap.add_argument("--game-dir", help="Path to the Factorio jc141 repack dir "
                                       "(auto-detected if omitted)")
    ap.add_argument("--check", action="store_true",
                    help="Inspect state and exit (no changes)")
    ap.add_argument("--force", action="store_true",
                    help="Force rebuild of the shim even if up to date")
    ap.add_argument("--skip-packages", action="store_true",
                    help="Do not install packages with pacman")
    ap.add_argument("--with-testing-tools", action="store_true",
                    help="Also install wtype/grim/imagemagick (test tools)")
    ap.add_argument("--verify-game", action="store_true",
                    help="After installing, launch the game and confirm it "
                         "reaches 'Factorio initialised' (needs a display session)")
    ap.add_argument("--reset", action="store_true",
                    help="Remove everything the fix generated (shims, config line)")
    ap.add_argument("--troubleshoot", action="store_true",
                    help="Run a full diagnostic report (re-runs the smoke test; "
                         "writes only to /tmp)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Answer yes to confirmation prompts")
    args = ap.parse_args()

    log("Factorio native-Wayland fix installer")
    if not distro_supports_pacman():
        raise SystemExit("ERROR: pacman not found. This installer targets Arch Linux "
                         "(the jc141 repack + Mesa/EGL setup this fix was developed on).")

    # Diagnostic/reset modes work even without a game dir (e.g. after the game
    # was deleted) -- the shim lives in $HOME, not in the game dir. Only the
    # install path requires the game.
    game_dir = find_game_dir(args.game_dir)
    if not game_dir and not (args.troubleshoot or args.reset or args.check):
        raise SystemExit("ERROR: could not find the Factorio game dir. "
                         "Pass --game-dir /path/to/Factorio_2.1.14")
    if game_dir:
        log("game dir: %s" % game_dir)
    else:
        log("game dir: not found (running %s in game-independent mode)"
            % ("--reset" if args.reset else "diagnostics"))

    if args.troubleshoot:
        run_troubleshoot(game_dir)
        sys.exit(0)

    if args.reset:
        if not args.yes:
            try:
                ans = input("This removes the shim + config wiring. Continue? [y/N] ")
            except EOFError:
                ans = "n"  # piped stdin (e.g. SUDO_STDIN=1) -> treat as no
            if ans.strip().lower() not in ("y", "yes"):
                log("aborted")
                sys.exit(1)
        uninstall_fix(game_dir)
        log("reset complete. Re-run without flags to reinstall.")
        sys.exit(0)

    if args.check:
        problems = inspect_state(game_dir)
        if problems:
            log("run the installer (no flags) to fix the %d problem(s) above" % problems, "!")
            sys.exit(2)
        log("everything looks good")
        sys.exit(0)

    ensure_executables(game_dir)

    pkgs = list(PACKAGES_FIX)
    if args.with_testing_tools:
        pkgs += PACKAGES_TEST_TOOLS
    install_packages(pkgs, args.skip_packages)
    check_prereqs()

    primary_dir, sandbox_dir, shim_path = shim_locations()
    shim = install_shims(primary_dir, sandbox_dir, force=args.force)
    remove_legacy_game_dir_shim(game_dir)

    cfg_path = os.path.join(game_dir, "local.config")
    wire_config(cfg_path)
    check_launcher_compat(game_dir)

    passed = run_smoke_test(shim)
    if not passed:
        raise SystemExit("ERROR: smoke test failed -- fix not verified. See output above.")

    if args.verify_game:
        print()
        game_ok = verify_game_launch(game_dir)
        if game_ok is False:
            raise SystemExit("ERROR: the game still failed to initialise -- fix NOT verified. "
                             "See the log above.")

    print()
    log("DONE. Factorio should now run on native Wayland: cd %s && ./start.n.sh" % game_dir)
    log("Shim installed: %s" % shim)
    log("Config: %s (ENV= line set; backup at local.config.bak if it existed)" % cfg_path)
    log("Diagnostic log written by the shim at /tmp/eglfix.log")
    if RICH:
        Console().print(Panel(
            "Game: [bold cyan]%s[/]\nShim: [bold green]%s[/]\nLaunch: [bold]cd %s && ./start.n.sh[/]"
            % (game_dir, shim, game_dir),
            title="Fix applied", border_style="green"))


if __name__ == "__main__":
    main()
