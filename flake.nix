{
  description = "Edge switcher for niri";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
    in {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          pythonEnv = pkgs.python3.withPackages (ps: with ps; [
            pygobject3
            pycairo
          ]);
          typelibPath = lib.concatStringsSep ":" [
            "${pkgs.glib.out}/lib/girepository-1.0"
            "${pkgs.glib.dev}/lib/girepository-1.0"
            "${pkgs.gdk-pixbuf.out}/lib/girepository-1.0"
            "${pkgs.gdk-pixbuf.dev}/lib/girepository-1.0"
            "${pkgs.graphene.out}/lib/girepository-1.0"
            "${pkgs.graphene.dev}/lib/girepository-1.0"
            "${pkgs.harfbuzz.out}/lib/girepository-1.0"
            "${pkgs.harfbuzz.dev}/lib/girepository-1.0"
            "${pkgs.gtk4.out}/lib/girepository-1.0"
            "${pkgs.gtk4.dev}/lib/girepository-1.0"
            "${pkgs.gtk4-layer-shell.out}/lib/girepository-1.0"
            "${pkgs.gtk4-layer-shell.dev}/lib/girepository-1.0"
            "${pkgs.pango.out}/lib/girepository-1.0"
            "${pkgs.pango.dev}/lib/girepository-1.0"
          ];
          layerShellPreload = "${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so";
          libraryPath = lib.makeLibraryPath [
            pkgs.cairo
            pkgs.gdk-pixbuf
            pkgs.glib
            pkgs.graphene
            pkgs.gtk4
            pkgs.gtk4-layer-shell
            pkgs.harfbuzz
            pkgs.libxkbcommon
            pkgs.pango
            pkgs.wayland
          ];
          schemaPath = lib.concatStringsSep ":" [
            "${pkgs.gsettings-desktop-schemas}/share/gsettings-schemas/${pkgs.gsettings-desktop-schemas.name}"
            "${pkgs.glib}/share/gsettings-schemas/${pkgs.glib.name}"
          ];
        in {
          default = pkgs.mkShell {
            packages = [
              pkgs.gobject-introspection
              pkgs.gsettings-desktop-schemas
              pkgs.gtk4-layer-shell
              pythonEnv
            ];

            shellHook = ''
              export GDK_BACKEND=wayland
              export GSK_RENDERER=gl
              export GI_TYPELIB_PATH="${typelibPath}''${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
              export LD_LIBRARY_PATH="${libraryPath}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
              export LD_PRELOAD="${layerShellPreload}''${LD_PRELOAD:+:$LD_PRELOAD}"
              export PYTHONUNBUFFERED=1
              export XDG_DATA_DIRS="${schemaPath}''${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
              echo "Run: python main.py"
            '';
          };
        });
    };
}
