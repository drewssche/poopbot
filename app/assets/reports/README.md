# Weekly report assets

Logo and day icon are raster copies of `web/demo/assets/logo.svg` and
`poop-day.svg`, exported at double report size with resvg-py 0.5.0.
Inter is the same font as the Mini App (`web/demo/assets/Inter-Variable.woff2`),
instantiated at weights 400, 500, 600 and 650 using fontTools 4.61.1.
The font license is included in OFL.txt. No system fonts or remote resources
are needed when rendering a report. Pillow is the only runtime renderer.
