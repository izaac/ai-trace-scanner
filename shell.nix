{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [ pkgs.uv pkgs.git pkgs.gnupg ];

  shellHook = ''
    echo "ai-trace-scan (nix-shell)"
    echo "Run: uv sync && uv run ai-trace-scan --help"
  '';
}
