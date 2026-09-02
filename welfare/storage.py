"""Storage per i file statici in produzione."""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class ForgivingManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Come la storage di WhiteNoise, ma un file mancante nel manifest non
    fa esplodere la pagina: viene servito il nome originale.

    Evita che un asset dimenticato in fase di collectstatic renda
    l'applicazione inutilizzabile in produzione.
    """

    manifest_strict = False
