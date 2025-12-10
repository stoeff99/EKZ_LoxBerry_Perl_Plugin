#!/bin/bash
# LoxBerry post-install for EKZ_LoxBerry_Perl_Plugin
# Sets executable bits for CGI, converts CRLF/BOM, and prepares data/config/log dirs.

# -------- Installer-provided args --------
COMMAND="$0"      # script name
PTEMPDIR="$1"     # temp folder during install
PSHNAME="$2"      # plugin short name for scripts
PDIR="$3"         # plugin install folder (should match FOLDER= in plugin.cfg)
PVERSION="$4"     # plugin version

# -------- Environment vars (from /etc/environment) --------
# See LoxBerry docs: LBPCGI/LBPHTML/LBPHTMLAUTH/LBPTEMPL/LBPDATA/LBPCONFIG/LBPLOG, etc.
# We'll use the plugin-target base + $PDIR to refer to the installed paths. [2](https://wiki.loxberry.de/entwickler/bash_supporting_scripts_for_your_plugin_development/systemweite_pfade_in_environmentvariablen)
PHTMLAUTH="$LBPHTMLAUTH/$PDIR"
PHTML="$LBPHTML/$PDIR"
PTEMPL="$LBPTEMPL/$PDIR"
PDATA="$LBPDATA/$PDIR"
PCONFIG="$LBPCONFIG/$PDIR"
PLOG="$LBPLOG/$PDIR"

echo "<INFO> postinstall.sh for $PDIR (version $PVERSION)"
echo "<INFO> Auth HTML path: $PHTMLAUTH"
echo "<INFO> Public HTML path: $PHTML"
echo "<INFO> Templates path: $PTEMPL"
echo "<INFO> Data path: $PDATA"
echo "<INFO> Config path: $PCONFIG"
echo "<INFO> Log path: $PLOG"

# -------- Ensure target dirs exist --------
mkdir -p "$PHTMLAUTH" "$PHTML" "$PTEMPL" "$PDATA" "$PCONFIG" "$PLOG"
chown -R loxberry:loxberry "$PHTMLAUTH" "$PHTML" "$PTEMPL" "$PDATA" "$PCONFIG" "$PLOG"

# -------- Helper to normalize line endings and strip BOM --------
fix_text_file() {
  local f="$1"
  # Remove CRLF line endings (Windows) and strip UTF-8 BOM at file start
  sed -i 's/\r$//' "$f"
  sed -i '1 s/^\xEF\xBB\xBF//' "$f"
}

# -------- Convert *.cgi to Unix LF and make executable --------
if [ -d "$PHTMLAUTH" ]; then
  for f in "$PHTMLAUTH"/*.cgi; do
    [ -f "$f" ] || continue
    fix_text_file "$f"
    chmod 0755 "$f"
    echo "<OK> Executable CGI set: $f"
  done
fi

if [ -d "$PHTML" ]; then
  for f in "$PHTML"/*.cgi; do
    [ -f "$f" ] || continue
    fix_text_file "$f"
    chmod 0755 "$f"
    echo "<OK> Executable CGI set: $f"
  done
fi

# -------- Static assets: *.html, *.css, *.js -> 0644 --------
for dir in "$PHTMLAUTH" "$PHTML" "$PTEMPL"; do
  [ -d "$dir" ] || continue
  find "$dir" -type f \( -name '*.html' -o -name '*.css' -o -name '*.js' \) -print0 | \
  while IFS= read -r -d '' f; do
    fix_text_file "$f"
    chmod 0644 "$f"
  done
done

# -------- Data & Config file perms --------
# Your settings file is PDATADIR/ekz_config.json (written by settings.cgi)
CFG="$PDATA/ekz_config.json"
[ -f "$CFG" ] && chmod 0640 "$CFG" && chown loxberry:loxberry "$CFG"

# -------- Create a log file (optional) --------
touch "$PLOG/install.log"
chmod 0644 "$PLOG/install.log"
chown loxberry:loxberry "$PLOG/install.log"

echo "<OK> postinstall.sh completed."
exit 0
