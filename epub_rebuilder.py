#!/usr/bin/env python3
import os
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
import argparse
from bs4 import BeautifulSoup
from ebooklib import epub
from pathlib import Path
from uuid import uuid4

# Register namespaces for XML parsing
ET.register_namespace('', 'http://www.idpf.org/2007/opf')
ET.register_namespace('dc', 'http://purl.org/dc/elements/1.1/')
ET.register_namespace('opf', 'http://www.idpf.org/2007/opf')
ET.register_namespace('epub', 'http://www.idpf.org/2007/ops')

def extract_epub(epub_path, extract_dir):
    """Extract EPUB file to a directory"""
    print(f"Extracting {epub_path} to {extract_dir}")
    
    # Create extract directory if it doesn't exist
    os.makedirs(extract_dir, exist_ok=True)
    
    # Extract the EPUB (which is just a ZIP file)
    with zipfile.ZipFile(epub_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    
    print("Extraction complete")

def find_content_opf(extract_dir):
    """Find the content.opf file in the extracted EPUB"""
    container_path = os.path.join(extract_dir, 'META-INF', 'container.xml')
    
    if not os.path.exists(container_path):
        raise FileNotFoundError(f"Could not find container.xml at {container_path}")
    
    # Parse container.xml to find content.opf
    tree = ET.parse(container_path)
    root = tree.getroot()
    
    # Find the rootfile element with media-type="application/oebps-package+xml"
    ns = {'ns': 'urn:oasis:names:tc:opendocument:xmlns:container'}
    rootfile_element = root.find('.//ns:rootfile[@media-type="application/oebps-package+xml"]', ns)
    
    if rootfile_element is None:
        # Try without namespace
        rootfile_element = root.find('.//rootfile[@media-type="application/oebps-package+xml"]')
    
    if rootfile_element is None:
        raise ValueError("Could not find content.opf reference in container.xml")
    
    content_opf_path = rootfile_element.get('full-path')
    return os.path.join(extract_dir, content_opf_path)

def parse_content_opf(content_opf_path):
    """Parse content.opf to get metadata, manifest, and spine"""
    tree = ET.parse(content_opf_path)
    root = tree.getroot()
    
    # Directory containing content.opf
    opf_dir = os.path.dirname(content_opf_path)
    
    # Parse manifest to get all items
    manifest = {}
    manifest_elem = root.find('.//{http://www.idpf.org/2007/opf}manifest')
    
    if manifest_elem is None:
        manifest_elem = root.find('.//manifest')
    
    if manifest_elem is None:
        raise ValueError("Could not find manifest in content.opf")
    
    for item in manifest_elem:
        item_id = item.get('id')
        item_href = item.get('href')
        item_media_type = item.get('media-type')
        manifest[item_id] = {
            'href': item_href,
            'media-type': item_media_type,
            'full_path': os.path.join(opf_dir, item_href)
        }
    
    # Parse spine to get reading order
    spine = []
    spine_elem = root.find('.//{http://www.idpf.org/2007/opf}spine')
    
    if spine_elem is None:
        spine_elem = root.find('.//spine')
    
    if spine_elem is None:
        raise ValueError("Could not find spine in content.opf")
    
    for itemref in spine_elem:
        idref = itemref.get('idref')
        if idref in manifest:
            spine.append(idref)
    
    return {
        'tree': tree,
        'root': root,
        'manifest': manifest,
        'spine': spine,
        'opf_dir': opf_dir
    }

def find_chapter_boundaries(content_data):
    """Identify chapters by splitting content on successive <h1> tags.

    The previous implementation grabbed the *entire* parent element of each
    <h1>.  When multiple chapters lived inside the same container (e.g. a
    single <div class="main"> that contained several chapter headings) this
    resulted in duplicate content – each chapter after the first inherited the
    prose that belonged to the earlier ones.  We now isolate the markup **from
    the <h1> tag up to (but not including) the next <h1> tag** so every chapter
    receives only its own section.
    """

    chapters: list[dict] = []

    # Collect XHTML documents from the spine that are genuine content pages
    content_files = []
    for item_id in content_data['spine']:
        item = content_data['manifest'].get(item_id)
        if not item:
            continue

        if item['media-type'] != 'application/xhtml+xml':
            continue

        # Exclude navigation/cover documents explicitly
        href_lower = item['href'].lower()
        if any(token in href_lower for token in ("nav.xhtml", "cover.xhtml")):
            continue

        content_files.append(item)

    from bs4.element import Tag  # local import keeps top-level imports untouched

    # We walk through the spine in order, allowing chapters to span multiple
    # files.  `current_chapter_*` hold the chapter we are presently building.

    current_title: str | None = None
    current_content: list[str] = []

    for item in content_files:
        with open(item["full_path"], "r", encoding="utf-8") as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, "html.parser")
        body = soup.body if soup.body else soup  # Fallback if <body> missing

        # Enumerate <h1> tags *in order* within this file.
        h1_tags = list(body.find_all("h1"))

        if not h1_tags:
            # No headings in this file; if we're inside a chapter, append the
            # whole body markup to it and continue.
            if current_title is not None:
                current_content.append(body.decode_contents())
            continue

        # There are one or more <h1> tags in this file.
        # We iterate over them, each time finalising the previous chapter (if
        # any) and starting a new one.

        # Use an iterator over the tag list so we can look at siblings for the
        # content slice.
        for idx, h1 in enumerate(h1_tags):
            # Whenever we encounter a heading, we first finish the *previous*
            # chapter (if one exists and we have accumulated content).
            if current_title is not None:
                chapters.append({
                    "title": current_title,
                    "content": "".join(current_content),
                    "id": f"ch_{len(chapters)}",
                })

            # Start the new chapter.
            current_title = h1.get_text(strip=True)
            current_content = [str(h1)]  # include the heading itself

            # Gather nodes until the next h1 *within this file*.
            for sibling in h1.next_siblings:
                if isinstance(sibling, Tag) and sibling.name == "h1":
                    break
                current_content.append(str(sibling))

        # End for h1 in file – if there were multiple headings we have already
        # closed all but the last. The last one remains open (current_* vars).

    # After processing all content files, flush the final chapter if pending.
    if current_title is not None:
        chapters.append({
            "title": current_title,
            "content": "".join(current_content),
            "id": f"ch_{len(chapters)}",
        })

    return chapters

def create_chapter_files(chapters, content_data):
    """Create new XHTML files for each chapter"""
    chapter_files = []
    
    # Get a template from an existing content file
    template_id = content_data['spine'][0]
    template_item = content_data['manifest'].get(template_id)
    template_path = template_item['full_path']
    
    with open(template_path, 'r', encoding='utf-8') as file:
        template_content = file.read()
    
    # Parse template to extract <head> content
    soup = BeautifulSoup(template_content, 'html.parser')
    head = soup.head
    
    # First, remove all old content files except cover and nav
    for item_id in list(content_data['spine']):
        item = content_data['manifest'].get(item_id)
        if item and item['media-type'] == 'application/xhtml+xml':
            if item['href'] != 'cover.xhtml' and item_id not in ('nav', 'cover') and not item_id.startswith('ch_'):
                file_path = item['full_path']
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Removed old file: {file_path}")
                    except Exception as e:
                        print(f"Warning: Could not remove file {file_path}: {e}")
    
    # Create a new file for each chapter
    for chapter in chapters:
        chapter_filename = f"{chapter['id']}.xhtml"
        chapter_path = os.path.join(content_data['opf_dir'], chapter_filename)
        
        # Create a new HTML document
        chapter_soup = BeautifulSoup('<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"></html>', 'html.parser')
        
        # Add head from template
        if head:
            chapter_soup.html.append(head)
        else:
            # Create a minimal head if none exists
            new_head = chapter_soup.new_tag('head')
            title_tag = chapter_soup.new_tag('title')
            title_tag.string = chapter['title']
            new_head.append(title_tag)
            
            # Add CSS link if available
            css_href = None
            for item in content_data['manifest'].values():
                if item['media-type'] == 'text/css':
                    css_href = item['href']
                    break
            
            if css_href:
                link_tag = chapter_soup.new_tag('link')
                link_tag['href'] = css_href
                link_tag['rel'] = 'stylesheet'
                link_tag['type'] = 'text/css'
                new_head.append(link_tag)
            
            chapter_soup.html.append(new_head)
        
        # Create body
        body = chapter_soup.new_tag('body')
        section = chapter_soup.new_tag('section', attrs={'epub:type': 'bodymatter chapter'})
        
        # Parse and add chapter content
        content_soup = BeautifulSoup(chapter['content'], 'html.parser')
        section.append(content_soup)
        
        body.append(section)
        chapter_soup.html.append(body)
        
        # Write the chapter file
        with open(chapter_path, 'w', encoding='utf-8') as file:
            file.write(str(chapter_soup))
        
        # Add to chapter files list
        chapter_files.append({
            'id': chapter['id'],
            'href': chapter_filename,
            'title': chapter['title'],
            'path': chapter_path
        })
    
    return chapter_files

def update_content_opf(content_data, chapter_files):
    """Update content.opf with new chapter files"""
    root = content_data['root']
    tree = content_data['tree']
    
    # Update manifest
    manifest_elem = root.find('.//{http://www.idpf.org/2007/opf}manifest')
    if manifest_elem is None:
        manifest_elem = root.find('.//manifest')
    
    # Remove old content items from manifest
    content_items_to_remove = []
    for item in manifest_elem:
        item_id = item.get('id')
        href = item.get('href')
        # Skip nav, ncx, and cover.xhtml; remove other XHTML that are not new chapters
        if (item_id not in ['nav', 'ncx'] and
            href != 'cover.xhtml' and
            item.get('media-type') == 'application/xhtml+xml' and
            not item_id.startswith('ch_')):
            content_items_to_remove.append(item)
    
    for item in content_items_to_remove:
        manifest_elem.remove(item)
    
    # Add new chapter items
    for chapter in chapter_files:
        # Check if item already exists
        existing_item = None
        for item in manifest_elem:
            if item.get('id') == chapter['id']:
                existing_item = item
                break
        
        if existing_item is not None:
            # Update existing item
            existing_item.set('href', chapter['href'])
            existing_item.set('media-type', 'application/xhtml+xml')
        else:
            # Add new item
            item = ET.SubElement(manifest_elem, '{http://www.idpf.org/2007/opf}item')
            item.set('id', chapter['id'])
            item.set('href', chapter['href'])
            item.set('media-type', 'application/xhtml+xml')
    
    # Update spine
    spine_elem = root.find('.//{http://www.idpf.org/2007/opf}spine')
    if spine_elem is None:
        spine_elem = root.find('.//spine')
    
    # Remove old content items from spine
    spine_items_to_remove = []
    for item in spine_elem:
        item_idref = item.get('idref')
        # Determine if this itemref points to cover.xhtml
        is_cover = False
        if item_idref in content_data['manifest']:
            is_cover = content_data['manifest'][item_idref]['href'] == 'cover.xhtml'
        # Skip nav, ncx, and cover; remove others
        if (item_idref not in ['nav', 'ncx'] and
            not is_cover and
            not item_idref.startswith('ch_')):
            spine_items_to_remove.append(item)
    
    for item in spine_items_to_remove:
        spine_elem.remove(item)
    
    # Remove any existing chapter items to avoid duplicates
    chapter_spine_items = []
    for item in spine_elem:
        if item.get('idref') and item.get('idref').startswith('ch_'):
            chapter_spine_items.append(item)
    
    for item in chapter_spine_items:
        spine_elem.remove(item)
    
    # Find the position to insert after cover and nav
    insert_position = 0
    for i, item in enumerate(spine_elem):
        item_idref = item.get('idref')
        if item_idref == 'nav':
            insert_position = i + 1
        else:
            # Check if this idref points to cover.xhtml
            if item_idref in content_data['manifest'] and content_data['manifest'][item_idref]['href'] == 'cover.xhtml':
                insert_position = i + 1
    
    # Insert new itemrefs at the correct position
    for i, chapter in enumerate(chapter_files):
        itemref = ET.Element('{http://www.idpf.org/2007/opf}itemref')
        itemref.set('idref', chapter['id'])
        spine_elem.insert(insert_position + i, itemref)
    
    # Write updated content.opf
    tree.write(os.path.join(content_data['opf_dir'], 'content.opf'), encoding='utf-8', xml_declaration=True)

def update_nav_document(content_data, chapter_files):
    """Update the navigation document with new chapters"""
    # Find the nav document
    nav_id = next((id for id in content_data['spine'] if id == 'nav'), None)
    
    if not nav_id:
        print("Navigation document not found. Skipping nav update.")
        return
    
    nav_item = content_data['manifest'].get(nav_id)
    nav_path = nav_item['full_path']
    
    # Parse the nav document
    with open(nav_path, 'r', encoding='utf-8') as file:
        nav_content = file.read()
    
    soup = BeautifulSoup(nav_content, 'html.parser')
    
    # Find the TOC nav element
    nav_elem = soup.find('nav', attrs={'epub:type': 'toc'})
    
    if not nav_elem:
        print("TOC nav element not found. Skipping nav update.")
        return
    
    # Find the ordered list
    ol = nav_elem.find('ol')
    
    if not ol:
        ol = soup.new_tag('ol')
        nav_elem.append(ol)
    else:
        # Clear existing TOC entries
        ol.clear()
    
    # Add cover entry if present
    cover_item = next((item for item in content_data['manifest'].values() if item['href'] == 'cover.xhtml'), None)
    if cover_item:
        li = soup.new_tag('li')
        a = soup.new_tag('a', href=cover_item['href'])
        a.string = "Cover"
        li.append(a)
        ol.append(li)
    
    # Add new chapter entries
    for chapter in chapter_files:
        li = soup.new_tag('li')
        a = soup.new_tag('a', href=chapter['href'])
        a.string = chapter['title']
        li.append(a)
        ol.append(li)
    
    # Write updated nav document
    with open(nav_path, 'w', encoding='utf-8') as file:
        file.write(str(soup))

def update_ncx_document(content_data, chapter_files):
    """Update the NCX document with new chapters"""
    # Find the NCX document
    ncx_id = next((id for id, item in content_data['manifest'].items() if item['media-type'] == 'application/x-dtbncx+xml'), None)
    
    if not ncx_id:
        print("NCX document not found. Skipping NCX update.")
        return
    
    ncx_item = content_data['manifest'].get(ncx_id)
    ncx_path = ncx_item['full_path']
    
    # Parse the NCX document
    tree = ET.parse(ncx_path)
    root = tree.getroot()
    
    # Find the navMap element
    ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
    nav_map = root.find('.//ncx:navMap', ns)
    
    if nav_map is None:
        nav_map = root.find('.//navMap')
    
    if nav_map is None:
        print("navMap element not found. Skipping NCX update.")
        return
    
    # Clear existing navPoints
    for nav_point in list(nav_map):
        nav_map.remove(nav_point)
    
    # Add new navPoints
    play_order = 1
    
    # Add cover navPoint if present
    cover_item = next((item for item in content_data['manifest'].values() if item['href'] == 'cover.xhtml'), None)
    if cover_item:
        nav_point = ET.SubElement(nav_map, 'navPoint')
        nav_point.set('id', f"navPoint-{play_order}")
        nav_point.set('playOrder', str(play_order))
        
        nav_label = ET.SubElement(nav_point, 'navLabel')
        text = ET.SubElement(nav_label, 'text')
        text.text = "Cover"
        
        content_elem = ET.SubElement(nav_point, 'content')
        content_elem.set('src', cover_item['href'])
        
        play_order += 1
    
    # Add chapter entries
    for chapter in chapter_files:
        nav_point = ET.SubElement(nav_map, 'navPoint')
        nav_point.set('id', f"navPoint-{play_order}")
        nav_point.set('playOrder', str(play_order))
        
        nav_label = ET.SubElement(nav_point, 'navLabel')
        text = ET.SubElement(nav_label, 'text')
        text.text = chapter['title']
        
        content = ET.SubElement(nav_point, 'content')
        content.set('src', chapter['href'])
        
        play_order += 1
    
    # Write updated NCX document
    tree.write(ncx_path, encoding='utf-8', xml_declaration=True)

def rebuild_epub(extract_dir, output_path):
    """Create a new EPUB file from the modified files"""
    print(f"Creating new EPUB at {output_path}")
    
    # Create a new EPUB
    book = epub.EpubBook()
    
    # Copy the mimetype file first (it must be uncompressed)
    with zipfile.ZipFile(output_path, 'w') as zip_file:
        mimetype_path = os.path.join(extract_dir, 'mimetype')
        if os.path.exists(mimetype_path):
            zip_file.write(mimetype_path, 'mimetype', compress_type=zipfile.ZIP_STORED)
    
    # Add all files to the EPUB
    with zipfile.ZipFile(output_path, 'a') as zip_file:
        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file == 'mimetype':
                    continue  # Already added
                
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, extract_dir)
                zip_file.write(file_path, arcname)
    
    print(f"EPUB created successfully at {output_path}")

def clean_up(extract_dir):
    """Remove temporary extraction directory"""
    shutil.rmtree(extract_dir)
    print(f"Removed temporary directory {extract_dir}")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Rebuild EPUB with chapter-based splitting')
    parser.add_argument('input_epub', help='Path to input EPUB file')
    parser.add_argument('output_epub', help='Path to output EPUB file')
    args = parser.parse_args()
    
    # Set output path
    output_path = args.output_epub
    
    # Create a temporary directory for extraction
    extract_dir = f"temp_epub_{uuid4().hex[:8]}"
    
    try:
        # Extract the EPUB
        extract_epub(args.input_epub, extract_dir)
        
        # Find and parse content.opf
        content_opf_path = find_content_opf(extract_dir)
        content_data = parse_content_opf(content_opf_path)
        
        # Find chapter boundaries
        chapters = find_chapter_boundaries(content_data)
        print(f"Found {len(chapters)} chapters")
        
        # Create new chapter files
        chapter_files = create_chapter_files(chapters, content_data)
        
        # Update content.opf
        update_content_opf(content_data, chapter_files)
        
        # Update navigation documents
        update_nav_document(content_data, chapter_files)
        update_ncx_document(content_data, chapter_files)
        
        # Create new EPUB
        rebuild_epub(extract_dir, output_path)
        
        print(f"EPUB successfully rebuilt with {len(chapters)} chapters at {output_path}")
    finally:
        # Clean up temporary files
        clean_up(extract_dir)

if __name__ == "__main__":
    main() 