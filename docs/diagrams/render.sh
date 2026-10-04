#!/usr/bin/env sh
# Render every diagram source in d2/ to its paired SVG in svg/.
# Sources whose basename starts with "_" are shared includes, not diagrams.
# Usage: docs/diagrams/render.sh            render all
#        docs/diagrams/render.sh NAME...    render only NAME.d2
set -eu
cd "$(dirname "$0")"
expected="v0.9.0"
actual="$(d2 --version)"
[ "$actual" = "$expected" ] || echo "warning: d2 $actual differs from the pinned $expected; exports may differ" >&2
if [ "$#" -gt 0 ]; then
  set -- "$@"
else
  set -- $(ls d2/*.d2 | sed 's#^d2/##; s#\.d2$##' | grep -v '^_')
fi
for name in "$@"; do
  d2 --sketch "d2/$name.d2" "svg/$name.svg"
done
