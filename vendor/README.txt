vendor/ holds third-party code the map serves as it is, each file next to its licence.

three-r180.js         three.js r180 (MIT, three.js authors): the core and module builds of the three@0.180.0 npm
                      package plus its Line2 / LineSegments2 / LineGeometry / LineSegmentsGeometry / LineMaterial and
                      Lensflare example modules, merged unmodified into one classic script that sets globalThis.THREE
                      (only module import and export statements were rewritten; the @license header stays)
LICENSE-three.txt     its licence; NOTICE carries the entry
xterm-6.0.0.js        xterm.js 6.0.0 (MIT, xterm.js authors): lib/xterm.js of the @xterm/xterm@6.0.0 npm package, as it is
xterm-6.0.0.css       css/xterm.css of the same package, as it is
LICENSE-xterm.txt     their licence; NOTICE carries the entry

The 3D view loads it on the first entry, from the bridge's /vendor/three-r180-<sha8>.js address (named by its digest,
cached for good) or, for a page rendered to disk, from the cm3d/ folder written beside the page (vendor3d.py).
Nothing in this folder is fetched from the network at run time. The three.js bundle combines the two npm build
files and the six example modules named above into a classic globalThis.THREE script. The original licence
headers and library implementation are retained. Its SHA-256 is recorded by the digest in vendor3d.py's URL.
