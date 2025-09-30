import json
import yaml
import os
from typing import Dict, Any, List, Union
import configparser
import toml

def extract_headers_and_bodies(file_path: str) -> List[Dict]:
    """
    Parses structured data files (JSON, YAML, TOML, INI) and returns a concise overview.
    Returns a list of dicts with:
      - node_type: 'structured_data'
      - name: filename
      - header: file type and basic stats
      - body: structural overview
    """
    _, ext = os.path.splitext(file_path.lower())
    filename = os.path.basename(file_path)
    
    try:
        if ext == '.json':
            return _parse_json_file(file_path, filename)
        elif ext in ('.yml', '.yaml'):
            return _parse_yaml_file(file_path, filename)
        elif ext in ('.toml', '.tml'):
            return _parse_toml_file(file_path, filename)
        elif ext in ('.ini', '.cfg', '.conf'):
            return _parse_ini_file(file_path, filename)
        else:
            raise ValueError(f"Unsupported structured data extension: {ext}")
    except Exception as e:
        # Fallback - treat as generic structured data
        return [{
            "node_type": "structured_data",
            "name": filename,
            "header": f"Structured data file ({ext}) - Parse error: {str(e)}",
            "body": None
        }]

def _should_expand_key(key: str, value: Any, parent_key: str = "") -> bool:
    """
    Determine if a key should be expanded to show more detail.
    This helps provide more useful information for common config patterns.
    """
    key_lower = key.lower()
    
    # Always expand small objects (< 10 keys)
    if isinstance(value, dict) and len(value) <= 10:
        return True
    
    # Always expand small arrays (< 5 items)  
    if isinstance(value, list) and len(value) <= 5:
        return True
    
    # Configuration-specific expansions
    config_keys = {
        'scripts', 'config', 'settings', 'options', 'env', 'environment',
        'database', 'db', 'server', 'client', 'api', 'auth', 'security',
        'logging', 'log', 'cache', 'redis', 'features', 'flags'
    }
    
    if key_lower in config_keys:
        return True
    
    # Package.json specific
    if key_lower in {'dependencies', 'devdependencies', 'peerdependencies'} and isinstance(value, dict):
        # For dependency objects, show a few examples
        return len(value) <= 20
    
    # Don't expand large generic objects
    return False

def _get_enhanced_overview(data: Any, filename: str) -> str:
    """
    Get enhanced overview with file-type specific intelligence
    """
    filename_lower = filename.lower()
    
    # Package lock files - show dependency info
    if 'package-lock' in filename_lower or 'yarn.lock' in filename_lower:
        if isinstance(data, dict) and 'packages' in data:
            base_overview = _analyze_structure(data, max_depth=3, key_name=filename)
            packages = data['packages']
            if isinstance(packages, dict):
                # Show a few example package structures
                sample_packages = list(packages.items())[:3]
                examples = []
                for pkg_name, pkg_data in sample_packages:
                    if isinstance(pkg_data, dict):
                        keys = list(pkg_data.keys())[:5]
                        examples.append(f"    {pkg_name}: {{{', '.join(keys)}}}")
                
                if examples:
                    base_overview += f"\n\nSample packages:\n" + "\n".join(examples)
                    if len(packages) > 3:
                        base_overview += f"\n  ... and {len(packages) - 3} more packages"
            
            return base_overview
    
    # Package.json - show dependencies more clearly
    elif filename_lower == 'package.json':
        if isinstance(data, dict):
            base_overview = _analyze_structure(data, max_depth=3, key_name=filename)
            
            # Add dependency summary
            dep_summary = []
            for dep_type in ['dependencies', 'devDependencies', 'peerDependencies']:
                if dep_type in data and isinstance(data[dep_type], dict):
                    count = len(data[dep_type])
                    if count <= 10:
                        deps = list(data[dep_type].keys())
                        dep_summary.append(f"  {dep_type}: {', '.join(deps)}")
                    else:
                        sample_deps = list(data[dep_type].keys())[:5]
                        dep_summary.append(f"  {dep_type}: {', '.join(sample_deps)} ... (+{count-5} more)")
            
            if dep_summary:
                base_overview += f"\n\nDependency details:\n" + "\n".join(dep_summary)
            
            return base_overview
    
    # Default analysis
    return _analyze_structure(data, max_depth=3, key_name=filename)

def _parse_json_file(file_path: str, filename: str) -> List[Dict]:
    """Parse JSON file and create structural overview"""
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            return [{
                "node_type": "structured_data",
                "name": filename,
                "header": f"JSON file - Invalid JSON: {str(e)}",
                "body": None,
                "example": None
            }]
    
    # Special handling for common file types
    overview = _get_enhanced_overview(data, filename)
    stats = _get_structure_stats(data)
    
    header = f"JSON file - {stats['type']} with {stats['key_count']} top-level keys" if stats['type'] == 'object' else f"JSON file - {stats['type']} with {stats['item_count']} items"
    
    stub = _make_struct_stub(data, max_keys=5, max_list=1)
    example = json.dumps(stub, indent=2, ensure_ascii=False)

    return [{
        "node_type": "structured_data",
        "name": filename,
        "header": header,
        "body": overview,
        "example": example
    }]

def _parse_yaml_file(file_path: str, filename: str) -> List[Dict]:
    """Parse YAML file and create structural overview"""
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return [{
                "node_type": "structured_data",
                "name": filename,  
                "header": f"YAML file - Invalid YAML: {str(e)}",
                "body": None,
                "example": None
            }]
    
    if data is None:
        return [{
            "node_type": "structured_data",
            "name": filename,
            "header": "YAML file - Empty or null content",
            "body": None,
            "example": None
        }]
    
    overview = _analyze_structure(data, max_depth=3)
    stats = _get_structure_stats(data)
    
    header = f"YAML file - {stats['type']} with {stats['key_count']} top-level keys" if stats['type'] == 'object' else f"YAML file - {stats['type']} with {stats['item_count']} items"
    
    stub = _make_struct_stub(data, max_keys=5, max_list=1)
    example = json.dumps(stub, indent=2, ensure_ascii=False)

    return [{
        "node_type": "structured_data",
        "name": filename,
        "header": header,
        "body": overview,
        "example": example
    }]

def _parse_toml_file(file_path: str, filename: str) -> List[Dict]:
    """Parse TOML file and create structural overview"""
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = toml.load(f)
        except Exception as e:
            return [{
                "node_type": "structured_data", 
                "name": filename,
                "header": f"TOML file - Parse error: {str(e)}",
                "body": None,
                "example": None
            }]
    
    overview = _analyze_structure(data, max_depth=3)
    stats = _get_structure_stats(data)
    
    header = f"TOML file - {stats['key_count']} sections/keys"

    stub = _make_struct_stub(data, max_keys=5, max_list=1)
    example = json.dumps(stub, indent=2, ensure_ascii=False)
    
    return [{
        "node_type": "structured_data",
        "name": filename,
        "header": header,
        "body": overview,
        "example": example
    }]

def _parse_ini_file(file_path: str, filename: str) -> List[Dict]:
    """Parse INI/CFG file and create structural overview"""
    config = configparser.ConfigParser()
    
    try:
        config.read(file_path, encoding='utf-8')
    except Exception as e:
        return [{
            "node_type": "structured_data",
            "name": filename,
            "header": f"INI/Config file - Parse error: {str(e)}",
            "body": None
        }]
    
    sections = list(config.sections())
    if config.defaults():
        sections.insert(0, 'DEFAULT')
    
    overview_lines = []
    for section in sections:
        if section == 'DEFAULT':
            keys = list(config.defaults().keys())
        else:
            keys = list(config[section].keys())
        
        if len(keys) <= 5:
            overview_lines.append(f"[{section}]: {', '.join(keys)}")
        else:
            overview_lines.append(f"[{section}]: {', '.join(keys[:5])} ... (+{len(keys)-5} more)")
    
    header = f"INI/Config file - {len(sections)} sections"
    body = "\n".join(overview_lines) if overview_lines else "No sections found"
    
    return [{
        "node_type": "structured_data",
        "name": filename,
        "header": header,
        "body": body
    }]

def _analyze_structure(data: Any, max_depth: int = 3, current_depth: int = 0, key_name: str = "") -> str:
    """
    Recursively analyze data structure and create a concise overview.
    Limits depth to avoid massive outputs for deeply nested data.
    """
    if current_depth >= max_depth:
        return f"<nested structure, depth limit reached>"
    
    if isinstance(data, dict):
        if not data:
            return "{}"
        
        lines = []
        # Show up to 10 keys to avoid huge outputs
        keys_to_show = list(data.keys())[:10]
        remaining = len(data) - len(keys_to_show)
        
        for key in keys_to_show:
            value = data[key]
            if isinstance(value, (dict, list)):
                if isinstance(value, dict):
                    value_desc = f"object({len(value)} keys)" if len(value) > 0 else "object(empty)"
                else:
                    value_desc = f"array({len(value)} items)" if len(value) > 0 else "array(empty)"
                
                # For complex nested structures, show brief preview
                if current_depth < max_depth - 1:
                    # Special handling for common config patterns
                    should_expand = _should_expand_key(key, value, key_name)
                    if should_expand and len(str(value)) < 500:
                        nested = _analyze_structure(value, max_depth, current_depth + 1, key)
                        if len(nested) < 200:  # Only show if reasonably short
                            lines.append(f"  {key}: {nested}")
                        else:
                            lines.append(f"  {key}: {value_desc}")
                    else:
                        lines.append(f"  {key}: {value_desc}")
                else:
                    lines.append(f"  {key}: {value_desc}")
            else:
                # For primitive values, show type and sample
                value_str = str(value)
                if len(value_str) > 50:
                    value_preview = value_str[:47] + "..."
                else:
                    value_preview = value_str
                lines.append(f"  {key}: {type(value).__name__}({value_preview})")
        
        result = "{\n" + "\n".join(lines) + "\n}"
        if remaining > 0:
            result += f"\n... and {remaining} more keys"
        
        return result
    
    elif isinstance(data, list):
        if not data:
            return "[]"
        
        # Show structure of first few items
        items_to_analyze = data[:5]
        remaining = len(data) - len(items_to_analyze)
        
        if len(set(type(item).__name__ for item in data)) == 1:
            # Homogeneous array
            item_type = type(data[0]).__name__
            if isinstance(data[0], (dict, list)):
                sample_structure = _analyze_structure(data[0], max_depth, current_depth + 1, "")
                return f"[{len(data)} items of type {item_type}]\nSample structure: {sample_structure}"
            else:
                sample_values = [str(item)[:30] + ("..." if len(str(item)) > 30 else "") for item in items_to_analyze]
                result = f"[{len(data)} items of type {item_type}]\nSample values: {', '.join(sample_values)}"
                if remaining > 0:
                    result += f" ... (+{remaining} more)"
                return result
        else:
            # Heterogeneous array
            lines = []
            for i, item in enumerate(items_to_analyze):
                item_desc = _analyze_structure(item, max_depth, current_depth + 1, f"[{i}]")
                if len(item_desc) > 100:
                    item_desc = f"{type(item).__name__}(...)"
                lines.append(f"  [{i}]: {item_desc}")
            
            result = "[\n" + "\n".join(lines) + "\n]"
            if remaining > 0:
                result += f"\n... and {remaining} more items"
            return result
    
    else:
        # Primitive value
        value_str = str(data)
        if len(value_str) > 100:
            return f"{type(data).__name__}({value_str[:97]}...)"
        return f"{type(data).__name__}({value_str})"

def _get_structure_stats(data: Any) -> Dict[str, Union[str, int]]:
    """Get basic statistics about the data structure"""
    if isinstance(data, dict):
        return {
            "type": "object",
            "key_count": len(data),
            "item_count": 0
        }
    elif isinstance(data, list):
        return {
            "type": "array", 
            "key_count": 0,
            "item_count": len(data)
        }
    else:
        return {
            "type": type(data).__name__,
            "key_count": 0,
            "item_count": 1
        }
    
def _make_struct_stub(data: Any, max_keys: int = 3, max_list: int = 1) -> Any:
    """
    Build a tiny stub of the data:
      - For dicts, include only the first max_keys keys (recursing).
      - For lists, include only the first max_list items (recursing).
      - Primitives get returned unchanged.
    """
    if isinstance(data, dict):
        stub = {}
        for k, v in list(data.items())[:max_keys]:
            stub[k] = _make_struct_stub(v, max_keys, max_list)
        return stub

    elif isinstance(data, list):
        return [
            _make_struct_stub(item, max_keys, max_list)
            for item in data[:max_list]
        ]
    else:
        # primitive: just show it
        return data
