import sys
import shutil
from gradio_client import Client, handle_file

def run_sadtalker(image_path, audio_path, output_path):
    print("Initializing Gradio Client for SadTalker...")
    client = Client("kevinwang676/SadTalker")
    
    print("Calling SadTalker prediction API...")
    result = client.predict(
        source_image=handle_file(image_path),
        driven_audio=handle_file(audio_path),
        preprocess="crop",
        still_mode=False,
        enhancer=True,
        batch_size=1,
        size="256",
        pose_style=0,
        api_name="/predict"
    )
    
    print(f"Prediction complete. Result: {result}")
    
    out_file = None
    if isinstance(result, str):
        out_file = result
    elif isinstance(result, dict) and "video" in result:
        out_file = result["video"]
    elif isinstance(result, list) and len(result) > 0:
        out_file = result[0]
        
    if out_file:
        shutil.copy2(out_file, output_path)
        print(f"Successfully saved to {output_path}")
    else:
        raise RuntimeError(f"Unexpected result format: {result}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(1)
        
    img = sys.argv[1]
    aud = sys.argv[2]
    out = sys.argv[3]
    run_sadtalker(img, aud, out)
