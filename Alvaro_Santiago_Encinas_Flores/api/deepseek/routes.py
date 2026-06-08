"""
DeepSeek routes — Sistema OSINT EMI

Expone el análisis de DeepSeek (capa post-BERT):
  - GET  /api/ai/deepseek/insights  → último resumen ejecutivo agregado
  - GET  /api/ai/deepseek/status    → conteo de analizados vs pendientes
  - POST /api/ai/deepseek/ejecutar  → dispara el análisis en segundo plano
"""
import json
import threading
import logging

from flask import Blueprint, jsonify, request

from api.common.database import get_db

logger = logging.getLogger("OSINT.API.DeepSeek")

bp = Blueprint('deepseek', __name__)

# Estado simple del job en memoria (un análisis a la vez)
_job_state = {'running': False, 'last_result': None}


def _table_exists(cursor, name) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


@bp.route('/api/ai/deepseek/insights')
def deepseek_insights():
    """Devuelve el último resumen ejecutivo generado por DeepSeek."""
    conn = get_db()
    cursor = conn.cursor()
    if not _table_exists(cursor, 'deepseek_resumen_global'):
        conn.close()
        return jsonify({'disponible': False, 'mensaje': 'Aún no se ha ejecutado el análisis DeepSeek.'})

    cursor.execute('''
        SELECT resumen_ejecutivo, sentimiento_general, temas_criticos_json,
               recomendaciones_json, carreras_destacadas_json, total_analizados, fecha
        FROM deepseek_resumen_global
        ORDER BY id DESC LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'disponible': False, 'mensaje': 'Aún no se ha ejecutado el análisis DeepSeek.'})

    def _parse(s):
        try:
            return json.loads(s or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    return jsonify({
        'disponible': True,
        'resumenEjecutivo': row['resumen_ejecutivo'],
        'sentimientoGeneral': row['sentimiento_general'],
        'temasCriticos': _parse(row['temas_criticos_json']),
        'recomendaciones': _parse(row['recomendaciones_json']),
        'carrerasDestacadas': _parse(row['carreras_destacadas_json']),
        'totalAnalizados': row['total_analizados'],
        'fecha': row['fecha'],
    })


@bp.route('/api/ai/deepseek/status')
def deepseek_status():
    """Conteo de ítems analizados por DeepSeek vs pendientes (ya pasaron por BERT)."""
    conn = get_db()
    cursor = conn.cursor()

    analizados = 0
    if _table_exists(cursor, 'analisis_deepseek'):
        cursor.execute("SELECT COUNT(*) c FROM analisis_deepseek")
        analizados = cursor.fetchone()['c']

    # Total elegible = posts con sentimiento BERT + comentarios con sentimiento BERT
    cursor.execute('''
        SELECT COUNT(*) c FROM dato_procesado dp
        JOIN analisis_sentimiento a ON dp.id_dato_procesado = a.id_dato_procesado
    ''')
    posts = cursor.fetchone()['c']
    cursor.execute('''
        SELECT COUNT(*) c FROM comentario c
        JOIN analisis_comentario ac ON c.id_comentario = ac.id_comentario
    ''')
    coments = cursor.fetchone()['c']
    conn.close()

    total = posts + coments
    return jsonify({
        'analizados': analizados,
        'total': total,
        'pendientes': max(0, total - analizados),
        'ejecutando': _job_state['running'],
        'ultimoResultado': _job_state['last_result'],
    })


@bp.route('/api/ai/deepseek/ejecutar', methods=['POST'])
def deepseek_ejecutar():
    """Dispara el análisis DeepSeek en segundo plano."""
    if _job_state['running']:
        return jsonify({'status': 'en_progreso',
                        'mensaje': 'Ya hay un análisis DeepSeek en ejecución.'}), 409

    force = bool(request.json.get('force')) if request.is_json else False

    def run_job():
        _job_state['running'] = True
        try:
            from deepseek_analyzer import analizar_con_deepseek
            _job_state['last_result'] = analizar_con_deepseek(force=force)
        except Exception as e:
            logger.exception("Error en análisis DeepSeek")
            _job_state['last_result'] = {'error': str(e)}
        finally:
            _job_state['running'] = False

    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({'status': 'iniciado',
                    'mensaje': 'Análisis DeepSeek iniciado en segundo plano.'})
