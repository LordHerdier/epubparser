# EPUB Chapter Rebuilder

A Python application that rebuilds EPUBs by properly splitting content based on chapter boundaries (using `<h1>` tags) rather than arbitrary divisions.

## Problem

Many EPUBs have their content split into arbitrary parts that don't align with actual chapters. This tool extracts the EPUB contents, identifies chapter boundaries by searching for `<h1>` tags, and rebuilds the EPUB with proper chapter-based splitting.

## Features

- Extracts EPUB files (which are essentially ZIP archives)
- Parses XHTML content files to find chapter boundaries based on `<h1>` tags
- Creates new chapter files with appropriate content
- Updates the content.opf, navigation document (nav.xhtml), and NCX file
- Rebuilds the EPUB with the proper chapter structure
- Preserves all original metadata, images, and styling

## Requirements

- Python 3.6+
- BeautifulSoup4
- EbookLib
- lxml

## Installation

1. Clone this repository or download the files
2. Install the required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

The script requires both input and output file paths:

```bash
python epub_rebuilder.py input.epub output.epub
```

This will extract the input EPUB, reorganize it according to chapter boundaries (identified by `<h1>` tags), and save the rebuilt EPUB to the output path.

## How It Works

1. The script extracts the EPUB file to a temporary directory
2. It locates and parses the content.opf file to understand the EPUB structure
3. It scans all content files for `<h1>` tags to identify chapter boundaries
4. It creates new XHTML files for each chapter
5. It updates the content.opf, navigation document, and NCX file with the new chapter structure
6. It rebuilds the EPUB file with the updated content
7. Finally, it cleans up the temporary files

## Limitations

- The script assumes that chapters are marked with `<h1>` tags
- It may not work correctly with EPUBs that have a highly customized structure
- Some e-readers may handle the rebuilt EPUB differently than the original

## License

MIT 