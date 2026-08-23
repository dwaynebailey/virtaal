# Settings for `dmgbuild` (https://dmgbuild.readthedocs.io/), used by
# devsupport/packaging/macos/build_dmg.sh and the build-macos-app CI job to
# turn dist/Virtaal.app into a real, drag-to-Applications .dmg installer.
#
# Modeled on gaphor/gaphor's own _packaging/dmgbuild-settings.py (confirmed
# via their real, current file - dmgbuild, not create-dmg, despite the
# latter also appearing in their CI's Homebrew install list; dmgbuild is
# pure Python and doesn't need Finder AppleScript automation the way
# create-dmg does, which matters for CI reliability).
#
# background/volume icon are devsupport/mac-bundle/virtaal_DMG_background.png
# and icons/VolumeIcon_virtaal.icns - reusable assets from the old, otherwise
# fully-dead 2011-era bundle (see ISSUE_TRIAGE.md's ".app bundle" entries);
# the background's two-circle-and-arrow artwork is 600x400, which
# window_rect/icon_locations below are matched to.
files = ["dist/Virtaal.app"]
symlinks = {"Applications": "/Applications"}
hide_extensions = ["Virtaal.app"]

volume_icon = "devsupport/mac-bundle/icons/VolumeIcon_virtaal.icns"
background = "devsupport/mac-bundle/virtaal_DMG_background.png"
window_rect = ((200, 120), (600, 400))

icon_size = 100
icon_locations = {
    "Virtaal.app": (150, 200),
    "Applications": (450, 200),
}
