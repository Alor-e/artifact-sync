import os
import sys

def extract_file_overview(file_path: str) -> list[dict]:
    """
    Dispatches to the correct language parser based on file extension.
    Supported extensions:
      - .py -> Python extractor
      - .cpp -> C++ extractor
      - .java -> Java extractor
      - .cs -> C# extractor
      - .js -> JavaScript extractor
      - .ts and .tsx -> Typescript extractor
      - .go -> Go extractor
      - .json, .yml, .yaml, .toml, .ini, .cfg, .conf -> Structured data extractor
    Returns a list of dicts {node_type, name, header, body}.
    """
    _, ext = os.path.splitext(file_path.lower())
    
    # Programming languages
    if ext == ".py":
        from parser.python_parser import extract_headers_and_bodies as extract_python
        return extract_python(file_path)
    elif ext in (".cpp", ".cc", ".cxx", ".h", ".hpp"):
        from parser.cpp_parser import extract_headers_and_bodies as extract_cpp
        return extract_cpp(file_path)
    elif ext == ".java":
        from parser.java_parser import extract_headers_and_bodies as extract_java
        return extract_java(file_path)
    elif ext == ".cs":
        from parser.c_sharp_parser import extract_headers_and_bodies as extract_c_sharp
        return extract_c_sharp(file_path)
    elif ext == ".js":
        from parser.javascript_parser import extract_headers_and_bodies as extract_js
        return extract_js(file_path)
    elif ext in (".ts", ".tsx"):
        from parser.typescript_parser import extract_headers_and_bodies as extract_ts
        return extract_ts(file_path)
    elif ext == ".go":
        from parser.go_parser import extract_headers_and_bodies as extract_go
        return extract_go(file_path)
    
    # Structured data files
    elif ext in (".json", ".yml", ".yaml", ".toml", ".tml", ".ini", ".cfg", ".conf"):
        from parser.structured_data_parser import extract_headers_and_bodies as extract_structured
        return extract_structured(file_path)
    
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python dispatcher.py <source-file>")
        sys.exit(1)

    file_path = sys.argv[1]
    try:
        overview = extract_file_overview(file_path)
        for entry in overview:
            print(entry)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)