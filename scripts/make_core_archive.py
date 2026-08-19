from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BOUND_ZIP = DATA_DIR / "hein-bound.zip"
DAILY_ZIP = DATA_DIR / "hein-daily.zip"
OUTPUT_ZIP = DATA_DIR / "stanford-core.zip"

CHUNK_SIZE = 1024 * 1024  # 1 MB


def copy_member(source_zip, source_name, target_zip, target_name):
    """Copy one ZIP member to another ZIP without extracting it to disk."""

    print(f"{source_name} -> {target_name}")

    with source_zip.open(source_name, "r") as src:
        with target_zip.open(target_name, "w") as dst:
            while True:
                chunk = src.read(CHUNK_SIZE)

                if not chunk:
                    break

                dst.write(chunk)


with (
    ZipFile(BOUND_ZIP, "r") as bound,
    ZipFile(DAILY_ZIP, "r") as daily,
    ZipFile(
        OUTPUT_ZIP,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as output,
):

    # Congress 43–111: use hein-bound
    for congress in range(43, 112):
        c = f"{congress:03d}"

        files = [
            f"speeches_{c}.txt",
            f"descr_{c}.txt",
            f"{c}_SpeakerMap.txt",
        ]

        for filename in files:
            source_name = f"hein-bound/{filename}"
            target_name = f"{c}/{filename}"

            copy_member(
                bound,
                source_name,
                output,
                target_name,
            )

    # Congress 112–114: use hein-daily
    for congress in range(112, 115):
        c = f"{congress:03d}"

        files = [
            f"speeches_{c}.txt",
            f"descr_{c}.txt",
            f"{c}_SpeakerMap.txt",
        ]

        for filename in files:
            source_name = f"hein-daily/{filename}"
            target_name = f"{c}/{filename}"

            copy_member(
                daily,
                source_name,
                output,
                target_name,
            )

print()
print(f"Created: {OUTPUT_ZIP}")
