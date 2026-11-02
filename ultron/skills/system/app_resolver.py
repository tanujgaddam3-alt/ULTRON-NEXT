import os
import shutil
import subprocess


class ApplicationResolver:
    """
    Finds installed applications.
    """

    def resolve(
        self,
        name: str,
    ):

        name = name.lower().strip()


        # 1. Executable search
        executable = shutil.which(name)

        if executable:
            return {
    "type": "binary",
    "path": executable,
}


        # 2. Desktop applications
        desktop = self.find_desktop_entry(name)

        if desktop:
            return {
    "type": "desktop",
    "path": desktop,
}


        # 3. Flatpak
        flatpak = self.find_flatpak(name)

        if flatpak:
            return flatpak


        return None



    def find_desktop_entry(
        self,
        name: str,
    ):

        locations = [
            "/usr/share/applications",
            os.path.expanduser(
                "~/.local/share/applications"
            ),
        ]


        for location in locations:

            if not os.path.exists(location):
                continue


            for file in os.listdir(location):

                if name in file.lower():

                    return os.path.join(
                        location,
                        file,
                    )


        return None



    def find_flatpak(
        self,
        name: str,
    ):

        try:

            result = subprocess.run(
                [
                    "flatpak",
                    "list",
                    "--app",
                ],
                capture_output=True,
                text=True,
            )


            for line in result.stdout.splitlines():

                if name in line.lower():

                    return line.split()[0]


        except Exception:

            pass


        return None
