{
  "md3": {<* for name, color in colors *>
    "{{ name }}": "{{ color.default.hex }}"<* if {{ loop.last }} *><* else *>,<* endif *><* endfor *>
  },
  "palette": {<* for name, palette in palettes *><* for shade, color in palette *>
    "{{ name }}{{ shade }}": "{{ color.hex }}"<* if {{ loop.last }} *><* else *>,<* endif *><* endfor *><* if {{ loop.last }} *><* else *>,<* endif *><* endfor *>
  },
  "base16": {<* for name, color in base16 *>
    "{{ name }}": "{{ color.default.hex }}"<* if {{ loop.last }} *><* else *>,<* endif *><* endfor *>
  }
}
