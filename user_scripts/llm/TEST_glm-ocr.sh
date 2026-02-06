#!/bin/bash                                                                                                        
                                                                                                                     
  # GLM-OCR Selection Script                                                                                         
  # Alternative to tesseract with better accuracy on complex documents                                               
                                                                                                                     
  # Configuration                                                                                                    
  MODEL="glm-ocr:bf16"                                                                                               
  TEMP_DIR="/tmp/glm-ocr"                                                                                            
  TEMP_IMAGE="$TEMP_DIR/screenshot.png"                                                                              
                                                                                                                     
  # Create temp directory if it doesn't exist                                                                        
  mkdir -p "$TEMP_DIR"                                                                                               
                                                                                                                     
  # Get OCR mode from argument (default: text)                                                                       
  MODE="${1:-text}"                                                                                                  
                                                                                                                     
  case "$MODE" in                                                                                                    
      text|t)                                                                                                        
          PROMPT="Text Recognition"                                                                                  
          ;;                                                                                                         
      table|tb)                                                                                                      
          PROMPT="Table Recognition"                                                                                 
          ;;                                                                                                         
      figure|fig|f)                                                                                                  
          PROMPT="Figure Recognition"                                                                                
          ;;                                                                                                         
      *)                                                                                                             
          PROMPT="Text Recognition"                                                                                  
          ;;                                                                                                         
  esac                                                                                                               
                                                                                                                     
  # Check if ollama is installed                                                                                     
  if ! command -v ollama &> /dev/null; then                                                                          
      notify-send "GLM-OCR Error" "Ollama is not installed"                                                          
      exit 1                                                                                                         
  fi                                                                                                                 
                                                                                                                     
  # Check if model is available                                                                                      
  if ! ollama list | grep -q "$MODEL"; then                                                                          
      notify-send "GLM-OCR" "Downloading model... This may take a moment"                                            
      ollama pull "$MODEL"                                                                                           
  fi                                                                                                                 
                                                                                                                     
  # Use slurp to select area, grim to capture, save to temp file                                                     
  if slurp | grim -g - "$TEMP_IMAGE"; then                                                                           
      # Show notification that OCR is processing                                                                     
      notify-send "GLM-OCR" "Processing ${MODE}..."                                                                  
                                                                                                                     
      # Run GLM-OCR and filter out status messages                                                                   
      RESULT=$(ollama run "$MODEL" "${PROMPT}: $TEMP_IMAGE" 2>&1 | \                                                 
          grep -v "^Added image" | \                                                                                 
          grep -v "^⠙" | \                                                                                           
          grep -v "^⠹" | \                                                                                           
          grep -v "^⠸" | \                                                                                           
          grep -v "^⠼" | \                                                                                           
          grep -v "^⠴" | \                                                                                           
          grep -v "^⠦" | \                                                                                           
          grep -v "^⠧" | \                                                                                           
          grep -v "^⠇" | \                                                                                           
          grep -v "^⠏" | \                                                                                           
          sed 's/^[[:space:]]*//;s/[[:space:]]*$//')                                                                 
                                                                                                                     
      if [ -n "$RESULT" ]; then                                                                                      
          echo -n "$RESULT" | wl-copy                                                                                
          notify-send "GLM-OCR" "Copied to clipboard!"                                                               
      else                                                                                                           
          notify-send "GLM-OCR Error" "No text detected"                                                             
      fi                                                                                                             
                                                                                                                     
      # Clean up                                                                                                     
      rm -f "$TEMP_IMAGE"                                                                                            
  else                                                                                                               
      notify-send "GLM-OCR" "Selection cancelled"                                                                    
  fi
