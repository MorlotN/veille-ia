"""Veille IA: pipeline RSS -> filtrage LLM local -> resume -> Telegram (texte + audio).

Remplace le workflow n8n du meme nom. Tout tourne en local:
Ollama pour le filtrage et le resume, Piper pour la synthese vocale.
"""

__version__ = "1.0.0"
