"""Bencode parsing utilities for LJS.

Provides byte-accurate recursive bencode decoding, specifically tracking 
the start and end indices of the 'info' key's value to allow SHA-1 hashing.
"""

from typing import Any, Tuple, Optional


class BencodeDecoder:
    """Byte-accurate recursive bencode decoder.

    Decodes standard bencoded byte payloads into Python primitives while
    tracking the exact byte offset indices of the 'info' dictionary value.
    """

    def decode_val(self, data: bytes, index: int) -> Tuple[Any, int, Optional[int], Optional[int]]:
        """Decode a bencoded value starting at a specific byte index.

        Args:
            data: Raw bencoded torrent bytes.
            index: Current reading index position.

        Returns:
            A tuple of (decoded_value, next_index, info_start_offset, info_end_offset).

        Raises:
            ValueError: If an unexpected EOF or malformed structure is encountered.
        """
        if index >= len(data):
            raise ValueError("Unexpected EOF")
        
        char = data[index:index+1]
        
        if char == b'i':
            end = data.find(b'e', index + 1)
            if end == -1:
                raise ValueError("Unterminated integer")
            return int(data[index+1:end]), end + 1, None, None
            
        elif char == b'l':
            res = []
            index += 1
            info_start, info_end = None, None
            while index < len(data) and data[index:index+1] != b'e':
                val, index, i_s, i_e = self.decode_val(data, index)
                if i_s is not None:
                    info_start, info_end = i_s, i_e
                res.append(val)
            return res, index + 1, info_start, info_end
            
        elif char == b'd':
            res = {}
            index += 1
            info_start, info_end = None, None
            while index < len(data) and data[index:index+1] != b'e':
                # Decode key
                key, index, _, _ = self.decode_val(data, index)
                if not isinstance(key, bytes):
                    raise ValueError("Dict key must be bytes")
                
                # Decode value
                val_start = index
                val, index, i_s, i_e = self.decode_val(data, index)
                
                if key == b'info':
                    info_start = val_start
                    info_end = index
                elif i_s is not None:
                    info_start, info_end = i_s, i_e
                
                res[key] = val
            return res, index + 1, info_start, info_end
            
        elif b'0' <= char <= b'9':
            colon = data.find(b':', index)
            if colon == -1:
                raise ValueError("Unterminated string length")
            length = int(data[index:colon])
            start = colon + 1
            end = start + length
            if end > len(data):
                raise ValueError("String length out of bounds")
            return data[start:end], end, None, None
            
        else:
            raise ValueError(f"Unknown type prefix: {char}")
