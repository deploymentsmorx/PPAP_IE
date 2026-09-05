# Image file metadata extraction.

from .base_extractor import create_document_template
from PIL import Image
import os
import piexif
import pytesseract
import imagehash
import numpy as np
from .output_paths import extracted_dir

def decode_barcodes(image):
    from pyzbar.pyzbar import decode

    return decode(image)

def extract_metadata(file_path):

    image = Image.open(file_path)

    metadata = {

        "file_name": os.path.basename(file_path),

        "format": image.format,

        "mode": image.mode,

        "mime_type": Image.MIME.get(image.format, ""),

        "is_animated": getattr(image, "is_animated", False),

        "frame_count": getattr(image, "n_frames", 1)

    }

    image.close()

    return metadata

def extract_image_properties(file_path):

    image = Image.open(file_path)

    width, height = image.size

    dpi = image.info.get("dpi", (None, None))

    properties = {

        "width": width,

        "height": height,

        "aspect_ratio": round(width / height, 4) if height else None,

        "megapixels": round((width * height) / 1_000_000, 2),

        "dpi_x": dpi[0] if dpi else None,

        "dpi_y": dpi[1] if dpi else None,

        "color_mode": image.mode,

        "bands": list(image.getbands()),

        "has_transparency": (
            image.mode in ("RGBA", "LA")
            or "transparency" in image.info
        )

    }

    image.close()

    return properties




def extract_exif(file_path):

    exif_data = {}

    try:

        exif_dict = piexif.load(file_path)

        for ifd_name in exif_dict:

            if ifd_name == "thumbnail":
                continue

            ifd_data = {}

            for tag_id, value in exif_dict[ifd_name].items():

                tag_name = piexif.TAGS[ifd_name][tag_id]["name"]

                if isinstance(value, bytes):

                    try:
                        value = value.decode("utf-8", errors="ignore")
                    except Exception:
                        value = str(value)

                elif isinstance(value, tuple):

                    value = list(value)

                ifd_data[tag_name] = value

            exif_data[ifd_name] = ifd_data

    except Exception:

        exif_data = {}

    return exif_data

def extract_ocr(file_path):

    ocr_results = []

    try:

        image = Image.open(file_path)

        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT
        )

        n = len(data["text"])

        for i in range(n):

            text = data["text"][i].strip()

            if text:

                ocr_results.append({

                    "text": text,

                    "confidence": float(data["conf"][i]),

                    "left": data["left"][i],

                    "top": data["top"][i],

                    "width": data["width"][i],

                    "height": data["height"][i]

                })

        image.close()

    except Exception as e:

        ocr_results.append({

            "error": str(e)

        })

    return ocr_results

def extract_qr(file_path):

    qr_codes = []

    try:

        image = Image.open(file_path)

        decoded_objects = decode_barcodes(image)

        qr_number = 1

        for obj in decoded_objects:

            if obj.type != "QRCODE":
                continue

            qr_codes.append({

                "qr_number": qr_number,

                "data": obj.data.decode("utf-8", errors="ignore"),

                "type": obj.type,

                "left": obj.rect.left,

                "top": obj.rect.top,

                "width": obj.rect.width,

                "height": obj.rect.height

            })

            qr_number += 1

        image.close()

    except Exception as e:

        qr_codes.append({

            "error": str(e)

        })

    return qr_codes


def extract_barcodes(file_path):

    barcodes = []

    try:

        image = Image.open(file_path)

        decoded_objects = decode_barcodes(image)

        barcode_number = 1

        for obj in decoded_objects:

            if obj.type == "QRCODE":
                continue

            barcodes.append({

                "barcode_number": barcode_number,

                "data": obj.data.decode("utf-8", errors="ignore"),

                "type": obj.type,

                "left": obj.rect.left,

                "top": obj.rect.top,

                "width": obj.rect.width,

                "height": obj.rect.height

            })

            barcode_number += 1

        image.close()

    except Exception as e:

        barcodes.append({

            "error": str(e)

        })

    return barcodes


def extract_thumbnail(file_path):

    thumbnail = {}

    try:

        image = Image.open(file_path)

        thumbnail_folder = str(extracted_dir("thumbnails"))

        os.makedirs(thumbnail_folder, exist_ok=True)

        file_name = os.path.splitext(
            os.path.basename(file_path)
        )[0]

        thumbnail_path = os.path.join(
            thumbnail_folder,
            f"{file_name}_thumbnail.jpg"
        )

        thumb = image.copy()

        thumb.thumbnail((256, 256))

        thumb.save(thumbnail_path, "JPEG")

        thumbnail = {

            "thumbnail_name": os.path.basename(thumbnail_path),

            "thumbnail_path": thumbnail_path,

            "width": thumb.width,

            "height": thumb.height

        }

        image.close()

    except Exception as e:

        thumbnail = {

            "error": str(e)

        }

    return thumbnail


def extract_hash(file_path):

    image = Image.open(file_path)

    hashes = {

        "average_hash": str(imagehash.average_hash(image)),

        "perceptual_hash": str(imagehash.phash(image)),

        "difference_hash": str(imagehash.dhash(image)),

        "wavelet_hash": str(imagehash.whash(image))

    }

    image.close()

    return hashes

def extract_dominant_colors(file_path):

    dominant_colors = []

    try:

        image = Image.open(file_path)

        image = image.convert("RGB")

        image = image.resize((200, 200))

        pixels = np.array(image)

        pixels = pixels.reshape(-1, 3)

        unique_colors, counts = np.unique(
            pixels,
            axis=0,
            return_counts=True
        )

        sorted_indices = np.argsort(counts)[::-1]

        top_colors = sorted_indices[:10]

        rank = 1

        for index in top_colors:

            color = unique_colors[index]

            dominant_colors.append({

                "rank": rank,

                "rgb": {

                    "red": int(color[0]),

                    "green": int(color[1]),

                    "blue": int(color[2])

                },

                "hex": "#{:02X}{:02X}{:02X}".format(
                    int(color[0]),
                    int(color[1]),
                    int(color[2])
                ),

                "pixel_count": int(counts[index]),

                "percentage": round(
                    (counts[index] / len(pixels)) * 100,
                    2
                )

            })

            rank += 1

        image.close()

    except Exception as e:

        dominant_colors.append({

            "error": str(e)

        })

    return dominant_colors


def extract(file_path):

    result = create_document_template(file_path)

    result["document"]["file_type"] = "image"

    result["metadata"] = extract_metadata(file_path)

    result["image_properties"] = extract_image_properties(file_path)

    result["exif"] = extract_exif(file_path)

    result["ocr"] = extract_ocr(file_path)

    result["qr_codes"] = extract_qr(file_path)

    result["barcodes"] = extract_barcodes(file_path)

    result["thumbnail"] = extract_thumbnail(file_path)

    result["image_hash"] = extract_hash(file_path)

    result["dominant_colors"] = extract_dominant_colors(file_path)

    return result
