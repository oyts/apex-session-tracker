"""Exact decoded screenshot identity; OCR statistics never prove duplication."""
import hashlib


def image_digest(image):
    return hashlib.sha256(image.convert('RGB').tobytes() + str(image.size).encode()).hexdigest()
