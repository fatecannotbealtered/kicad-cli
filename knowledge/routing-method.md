---
id: routing-method
title: Routing a board
kind: guide
applies_to: [track create, via create, zone create, track delete, track list, zone list, footprint list, board check, project get]
tags: [routing, tracks, vias, zones, workflow]
---

Routing with kicad-cli is a loop: read what is left, draw a batch, check.

1. The rules: `project get --doc <the .kicad_pro>` gives each net class's track width, clearance
   and via size, the copper-to-edge clearance, and which nets belong to which class.
2. What is left: `board check --severity error` lists each unconnected item with the pads or
   tracks it joins and their positions. `footprint list --fields id,pads` gives every pad's
   centre, net, type and the box its copper takes.
3. Order: supply nets first, at the width asked, then the signals at their net class width.
   A ground plane is one `zone create` on a layer, usually the bottom, over the board outline.
4. Draw each connection with `track create --points`, a path through its corners, from pad
   centre to pad centre. Corners at 45 or 90 degrees route most boards. Keep each segment, half
   its width plus the clearance, away from pads and tracks of other nets; between the pads of a
   fine-pitch part there is room for at most one thin track.
5. Vias come after the tracks they join: put each at the end of a track already drawn, or inside
   a zone of its net. A via that touches nothing yet adds an unconnected item and is refused.
   With a ground plane on the bottom, a surface-mount ground pad needs a short track to a via
   beside it; through-hole ground pads reach the plane themselves.
6. Send several tracks or vias in one `--input` file: KiCad's DRC checks them together once, and
   the preview says how many unconnected items are left in all. A call that would add a DRC
   error, such as a short or a clearance violation, is refused, and the preview lists the errors
   with the objects they involve: change the paths and send it again. Each item's result says
   what its ends touch.
7. Check with `board check` after each batch: it refills the zones in memory, so a plane's
   connections show there, and the unconnected items left say what to route next.
8. A wrong track or via: `track list` gives its UUID, `track delete` removes it.

Done means `board check` reports no errors and no unconnected items.
