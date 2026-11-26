# FSM y Navegación Autónoma — RoboUAQ

Aplica cuando: trabajes en nav_node, lógica de decisiones, o comportamiento autónomo.

## Estados de la FSM

```
IDLE
  └─(start) → FOLLOW_LANE
                └─(crosswalk o AprilTag en rango) → APPROACHING_INTERSECTION
                                                      └─(vel=0) → STOPPED_AT_INTERSECTION
                                                                    └─(3s timer) → EXECUTE_TURN
                                                                                    └─(completo) → FOLLOW_LANE
FOLLOW_LANE
  └─(alto detectado) → FINISH  (parada permanente)

Cualquier estado + ESTOP → STOPPED (inmediato, vel=0)
Cualquier estado + modo MANUAL → FSM pausa
```

## Señales y acciones

| Señal | Acción en intersección |
|-------|----------------------|
| `no_pasar` | No entrar esa calle, elegir alternativa |
| `sin_salida` | No entrar esa calle, elegir alternativa |
| `vuelta_derecha` | Girar derecha |
| `vuelta_izquierda` | Girar izquierda |
| `siga_de_frente` | Ir recto |
| `alto` | STOP permanente = meta alcanzada |

## Velocidades objetivo

| Situación | Velocidad |
|-----------|-----------|
| Lane following normal | 0.3 m/s |
| Aproximándose a intersección | 0.15 m/s |
| Ejecutando giro | 0.2 m/s |
| ESTOP | 0.0 m/s (inmediato) |

## Reglas críticas del reglamento

- **OBLIGATORIO:** Detener 3 segundos ANTES de cruzar cada intersección (penalización: -10 pts)
- Confirmar señal en ≥3 frames consecutivos antes de ejecutar (evitar falsos positivos)
- Cooldown por tag_id: 5 segundos (evitar re-activar la misma señal)
- **Regla 3.10:** 20 segundos inmóvil = recorrido terminado → incluir safety timer
- Timeout de señal: si no se confirma en 2s, volver a FOLLOW_LANE
- Máx 3 penalizaciones antes de terminación

## Comportamiento por modo

- **manual**: FSM no procesa, no publica nada
- **observe**: FSM corre y calcula decisiones completas, publica `/nav/state` y `/nav/decision_log`, NO publica `/cmd_vel` ni `/cmd_steer`
- **auto**: FSM corre y publica todo incluyendo `/cmd_vel` y `/cmd_steer`

## Prioridad de publicación en /cmd_vel

1. ESTOP (Y en Xbox) — SIEMPRE gana, override inmediato → 0.0
2. Modo MANUAL — teleop_xbox_node publica directamente
3. Modo AUTO — nav_node publica
4. Modo OBSERVE — nav_node NO publica (solo logs)

## Parámetros de steering

```
steering_error (Float32, -1.0 a 1.0) → offset µs
cmd_steer (Int32) = 1500 + steering_error × gain
gain típico: 300 (ajustar en pruebas)
Límites físicos: 1200µs (izq) — 1500µs (centro) — 1800µs (der)
```

## Publicaciones de diagnóstico

- `/nav/state`: nombre del estado actual (ej: "FOLLOW_LANE", "STOPPED_AT_INTERSECTION")
- `/nav/decision_log`: entrada legible por humanos (ej: "Detectado vuelta_derecha a 0.8m → girando derecha")
- Útiles para dashboard en modo OBSERVE — ver qué haría el sistema autónomo
