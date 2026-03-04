import fitz  # PyMuPDF
import os
import re
from datetime import datetime

def extract_header_info(page):
    """
    Scans the top of the page for Bulletin Number and Date.
    Returns: (bulletin_number, formatted_date) or (None, None)
    """
    header_rect = fitz.Rect(0, 0, page.rect.width, 150)
    header_text = page.get_text("text", clip=header_rect)
    header_text = " ".join(header_text.split())

    bulletin_match = re.search(r"\d{4}/(\d+)", header_text)
    bulletin_no = bulletin_match.group(1) if bulletin_match else None

    date_match = re.search(r"Yayın Tarihi\s*[:]\s*(\d{2}\.\d{2}\.\d{4})", header_text)
    formatted_date = None
    
    if date_match:
        raw_date = date_match.group(1)
        try:
            date_obj = datetime.strptime(raw_date, "%d.%m.%Y")
            formatted_date = date_obj.strftime("%Y-%m-%d")
        except ValueError:
            formatted_date = raw_date.replace(".", "-")

    return bulletin_no, formatted_date

def extract_images_from_pdf(pdf_path, base_output_dir):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return

    print(f"Processing: {pdf_path}")
    image_count = 0
    
    # Default path if no header found
    current_save_path = os.path.join(base_output_dir, "Unknown_Bulletin", "images")

    for page_index, page in enumerate(doc):
        # 1. Update Header Info per page (in case it changes or is only on some pages)
        b_no, b_date = extract_header_info(page)
        if b_no and b_date:
            folder_name = f"{b_no}_{b_date}"
            current_save_path = os.path.join(base_output_dir, folder_name, "images")
        
        if not os.path.exists(current_save_path):
            os.makedirs(current_save_path, exist_ok=True)

        # 2. Get all images
        image_list = page.get_images(full=True)
        if not image_list:
            continue
            
        print(f"Page {page_index + 1}: Found {len(image_list)} images.")

        for img_index, img in enumerate(image_list):
            xref = img[0]
            
            # Get where this specific image is drawn on the page
            image_rects = page.get_image_rects(xref)
            
            # If get_image_rects is empty, the image might be hidden or unused
            if not image_rects:
                continue

            # We iterate through 'rects' just in case the same image object 
            # is used in multiple places (though rare for unique IDs).
            # Usually len(image_rects) is 1.
            for rect_index, rect in enumerate(image_rects):
                
                # --- PRECISE SEARCH STRATEGY ---
                # We want the ID at the TOP-LEFT corner.
                # Avoid searching the full width (rect.x1) to prevent reading neighbor's ID.
                
                # Box Definition:
                # Left: rect.x0 - 20 (Slightly left of the image edge)
                # Top:  rect.y0 - 60 (Look up 60 pixels)
                # Right: rect.x0 + 120 (Only look 120px to the right of the left edge)
                # Bottom: rect.y0 + 10 (Slightly inside the image top, to catch border overlaps)
                
                search_rect = fitz.Rect(
                    rect.x0 - 20, 
                    rect.y0 - 60, 
                    rect.x0 + 120, 
                    rect.y0 + 10
                )

                nearby_text = page.get_text("text", clip=search_rect)
                
                # Regex: Look for pattern YYYY/XXXX
                # \s* allows for spaces like "2006 / 15591" or newlines
                match = re.search(r"(\d{4})\s*/\s*(\d+)", nearby_text)
                
                final_filename = ""
                
                if match:
                    found_id = f"{match.group(1)}_{match.group(2)}" # 2006_15591
                    final_filename = f"{found_id}"
                    print(f"  -> [Pos {rect_index}] Found ID: {found_id}")
                else:
                    # Fallback name if no text found
                    final_filename = f"page{page_index+1}_img{img_index+1}_pos{rect_index}"
                    print(f"  -> [Pos {rect_index}] No ID found. Using: {final_filename}")

                # --- EXTRACT AND SAVE ---
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    full_filename = f"{final_filename}.{image_ext}"
                    save_path = os.path.join(current_save_path, full_filename)

                    # Only save if it doesn't exist (or overwrite if you prefer)
                    if not os.path.exists(save_path):
                        with open(save_path, "wb") as f:
                            f.write(image_bytes)
                        image_count += 1
                except Exception as e:
                    print(f"     Error saving: {e}")

    print(f"\nDone! Extracted {image_count} images.")

if __name__ == "__main__":
    pdf_source_dir = r"C:\Users\sdogan\turk_patent\bulletins\Marka\IMAGES"
    save_dir = r"C:\Users\sdogan\turk_patent\bulletins\Marka"
    
    if os.path.exists(pdf_source_dir):
        pdf_files = [f for f in os.listdir(pdf_source_dir) if f.lower().endswith('.pdf')]
        for filename in pdf_files:
            full_pdf_path = os.path.join(pdf_source_dir, filename)
            print(f"\n--- Processing: {filename} ---")
            extract_images_from_pdf(full_pdf_path, save_dir)
    else:
        print(f"Directory not found: {pdf_source_dir}")