#!/bin/bash
# medir_jetson.sh: foto del estado de la Jetson (solo lectura, no cambia nada).
#
# Uso:  scripts/medir_jetson.sh "F0 antes"            # etiqueta libre; promedia 30 s de tegrastats
#       scripts/medir_jetson.sh "F0 despues" 60        # segundos de tegrastats (por defecto 30)
#
# Imprime un resumen y agrega una linea a ~/robocar_ws/mediciones_jetson.tsv (evidencia para el
# articulo y para comparar cada fase de la optimizacion).
#
# Columnas: etiqueta, fecha, MemAvailable_MB, usada_MB, cache_MB, libre_MB, CmaFree_MB, zram_usado_MB,
#           servicios_sistema, servicios_usuario, CPU_%, VDD_IN_mW, tj_C_prom, tj_C_max, GR3D_%
set -u
ETIQUETA="${1:-sin etiqueta}"
SEG="${2:-30}"
TSV="$HOME/robocar_ws/mediciones_jetson.tsv"

kb() { awk -v k="$1" '$1==k":" {printf "%.0f", $2/1024}' /proc/meminfo; }
disp=$(kb MemAvailable); libre=$(kb MemFree); cache=$(kb Cached); cma=$(kb CmaFree)
total=$(kb MemTotal)
usada=$(free -m | awk '/^Mem:/ {print $3}')
zram=$(awk 'NR>1 && $1 ~ /zram/ {s+=$4} END {printf "%.0f", s/1024}' /proc/swaps)
svc_sis=$(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | wc -l)
svc_usr=$(systemctl --user list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | wc -l)

# tegrastats: promedio de SEG segundos
TMP=$(mktemp)
timeout "$SEG" tegrastats --interval 1000 > "$TMP" 2>/dev/null
read -r cpu vdd tjp tjm gr n <<<"$(awk '
  { n++
    if (match($0, /CPU \[[^]]*\]/)) {
      c = substr($0, RSTART+5, RLENGTH-6); k = split(c, a, ","); s = 0
      for (i = 1; i <= k; i++) { split(a[i], b, "%"); s += b[1] }
      cpu += s / k }
    if (match($0, /VDD_IN [0-9]+mW/)) { v = substr($0, RSTART+7, RLENGTH-9); vdd += v }
    if (match($0, /tj@[0-9.]+C/))     { t = substr($0, RSTART+3, RLENGTH-4); tj += t; if (t > tm) tm = t }
    if (match($0, /GR3D_FREQ [0-9]+%/)) { g = substr($0, RSTART+10, RLENGTH-11); gr += g }
  }
  END { if (n == 0) n = 1; printf "%.0f %.0f %.1f %.1f %.0f %d", cpu/n, vdd/n, tj/n, tm, gr/n, n }' "$TMP")"
rm -f "$TMP"

fecha=$(date '+%F %H:%M:%S')
echo "=== $ETIQUETA  ($fecha) ==="
printf 'RAM: usada %s MB de %s | disponible %s | libre %s | cache %s | CmaFree %s MB | zram usado %s MB\n' "$usada" "$total" "$disp" "$libre" "$cache" "$cma" "$zram"
printf 'Servicios corriendo: %s de sistema + %s de usuario\n' "$svc_sis" "$svc_usr"
printf 'tegrastats (%s muestras): CPU %s%% | VDD_IN %s mW | tj %s C (max %s) | GR3D %s%%\n' "$n" "$cpu" "$vdd" "$tjp" "$tjm" "$gr"
echo "Top 6 por memoria (RSS MB):"
ps -eo rss,comm --sort=-rss | awk 'NR>1 && NR<=7 {printf "   %6.0f  %s\n", $1/1024, $2}'

[ -f "$TSV" ] || printf 'etiqueta\tfecha\tMemAvailable_MB\tusada_MB\tcache_MB\tlibre_MB\tCmaFree_MB\tzram_usado_MB\tsvc_sistema\tsvc_usuario\tCPU_pct\tVDD_IN_mW\ttj_C_prom\ttj_C_max\tGR3D_pct\n' > "$TSV"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$ETIQUETA" "$fecha" "$disp" "$usada" "$cache" "$libre" "$cma" "$zram" "$svc_sis" "$svc_usr" "$cpu" "$vdd" "$tjp" "$tjm" "$gr" >> "$TSV"
echo "(agregado a $TSV)"
