from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses a Go file and returns a list of dicts with:
      - node_type: e.g. 'package_clause', 'import_declaration', 'type_declaration',
                   'function_declaration', 'method_declaration', 'var_declaration', 'const_declaration'
      - name: identifier for types, functions, methods, or None if not applicable
      - header: the source text for the signature (or entire package/import line)
      - body: the source text for the body block (or None if it's a single-line declaration)
    
    Focuses on top-level declarations that give a good overview of the file's purpose:
      - Package and imports (essential context)
      - Public types (structs, interfaces, custom types)
      - Public functions and methods
      - Package-level variables and constants
      - Init functions (always captured regardless of visibility)
    
    Filters out private (lowercase) functions/types unless they're commonly important
    (like init functions or main function).
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("go")
    tree = parser.parse(source_bytes)
    root = tree.root_node  # usually a 'source_file'

    results = []
    
    for node in root.named_children:
        nt = node.type

        # 1. Package clause (always first in Go files)
        if nt == "package_clause":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2. Import declarations
        if nt == "import_declaration":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 3. Type declarations (struct, interface, custom types)
        if nt == "type_declaration":
            _process_go_type_declaration(node, source_bytes, results)
            continue

        # 4. Function declarations
        if nt == "function_declaration":
            _process_go_function(node, source_bytes, results)
            continue

        # 5. Method declarations (functions with receivers)
        if nt == "method_declaration":
            _process_go_method(node, source_bytes, results)
            continue

        # 6. Variable declarations at package level
        if nt == "var_declaration":
            _process_go_var_declaration(node, source_bytes, results)
            continue

        # 7. Constant declarations at package level
        if nt == "const_declaration":
            _process_go_const_declaration(node, source_bytes, results)
            continue

        # 8. Everything else: skip (comments, etc.)
        continue

    return results


def _process_go_type_declaration(type_node, source_bytes: bytes, out_list: list):
    """
    Processes Go type declarations like:
    - type MyStruct struct { ... }
    - type MyInterface interface { ... }
    - type MyInt int
    - type ( ... ) // type group
    
    Only captures public types (capitalized names) unless it's a significant structural type.
    """
    # Check if this is a type group: type ( ... )
    type_spec_list = None
    for child in type_node.named_children:
        if child.type == "type_spec_list":
            type_spec_list = child
            break
    
    if type_spec_list:
        # Handle grouped type declarations: type ( TypeA struct{...}; TypeB interface{...} )
        for spec in type_spec_list.named_children:
            if spec.type == "type_spec":
                _process_single_type_spec(spec, source_bytes, out_list)
    else:
        # Handle single type declaration: type MyType struct{...}
        for child in type_node.named_children:
            if child.type == "type_spec":
                _process_single_type_spec(child, source_bytes, out_list)
                break


def _process_single_type_spec(type_spec, source_bytes: bytes, out_list: list):
    """Process a single type specification within a type declaration."""
    name_node = type_spec.child_by_field_name("name")
    if not name_node:
        return
    
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
    
    # Skip private types (lowercase) unless they seem structurally important
    if not _is_public_or_important_go_name(name):
        return
    
    type_node = type_spec.child_by_field_name("type")
    if not type_node:
        # Simple type alias: type MyInt int
        header = source_bytes[type_spec.start_byte : type_spec.end_byte].decode("utf8")
        out_list.append({
            "node_type": "type_declaration",
            "name": name,
            "header": header,
            "body": None
        })
        return
    
    # Complex types with bodies (struct, interface)
    if type_node.type in ("struct_type", "interface_type"):
        # Find the body (field_declaration_list or method_spec_list)
        body_node = None
        for child in type_node.named_children:
            if child.type in ("field_declaration_list", "method_spec_list"):
                body_node = child
                break
        
        if body_node:
            header = source_bytes[type_spec.start_byte : body_node.start_byte].decode("utf8")
            body = source_bytes[body_node.start_byte : type_spec.end_byte].decode("utf8")
        else:
            header = source_bytes[type_spec.start_byte : type_spec.end_byte].decode("utf8")
            body = None
    else:
        # Other type definitions (function types, channel types, etc.)
        header = source_bytes[type_spec.start_byte : type_spec.end_byte].decode("utf8")
        body = None
    
    out_list.append({
        "node_type": "type_declaration",
        "name": name,
        "header": header,
        "body": body
    })


def _process_go_function(func_node, source_bytes: bytes, out_list: list):
    """Process function declarations, filtering for public functions and special cases."""
    name_node = func_node.child_by_field_name("name")
    if not name_node:
        return
    
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
    
    # Always capture init and main functions, otherwise only public functions
    if not _is_public_or_important_go_name(name) and name not in ("init", "main"):
        return
    
    body_node = func_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[func_node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : func_node.end_byte].decode("utf8")
    else:
        # Function declaration without body (shouldn't happen in Go, but just in case)
        header = source_bytes[func_node.start_byte : func_node.end_byte].decode("utf8")
        body = None
    
    out_list.append({
        "node_type": "function_declaration",
        "name": name,
        "header": header,
        "body": body
    })


def _process_go_method(method_node, source_bytes: bytes, out_list: list):
    """Process method declarations (functions with receivers)."""
    name_node = method_node.child_by_field_name("name")
    if not name_node:
        return
    
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
    
    # Only capture public methods
    if not _is_public_or_important_go_name(name):
        return
    
    body_node = method_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[method_node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : method_node.end_byte].decode("utf8")
    else:
        header = source_bytes[method_node.start_byte : method_node.end_byte].decode("utf8")
        body = None
    
    out_list.append({
        "node_type": "method_declaration",
        "name": name,
        "header": header,
        "body": body
    })


def _process_go_var_declaration(var_node, source_bytes: bytes, out_list: list):
    """Process package-level variable declarations."""
    # Only capture if it looks like a significant package-level variable
    header = source_bytes[var_node.start_byte : var_node.end_byte].decode("utf8")
    
    # Quick heuristic: skip very simple declarations like "var x int"
    # Focus on initialized variables or those that seem important
    if "=" in header or len(header) > 20 or _contains_public_identifier(header):
        out_list.append({
            "node_type": "var_declaration",
            "name": None,
            "header": header,
            "body": None
        })


def _process_go_const_declaration(const_node, source_bytes: bytes, out_list: list):
    """Process package-level constant declarations."""
    header = source_bytes[const_node.start_byte : const_node.end_byte].decode("utf8")
    
    # Constants are often important for understanding a package's API
    out_list.append({
        "node_type": "const_declaration",
        "name": None,
        "header": header,
        "body": None
    })


def _is_public_or_important_go_name(name: str) -> bool:
    """
    Check if a Go identifier is public (starts with uppercase) or important.
    In Go, public identifiers start with uppercase letters.
    """
    if not name:
        return False
    return name[0].isupper() or name in ("init", "main")


def _contains_public_identifier(text: str) -> bool:
    """Quick check if the text contains what looks like a public identifier."""
    import re
    # Look for capitalized identifiers that might be public
    return bool(re.search(r'\b[A-Z][a-zA-Z0-9_]*\b', text))