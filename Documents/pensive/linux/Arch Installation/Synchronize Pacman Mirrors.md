
For an online installation, configure the pacman mirror list before installing the base system. For an offline ISO, use its configured local repositories and skip this online step. The `reflector` utility can select mirrors using reported freshness and measured rates; availability and results depend on the live environment and network.

> [!TIP] Why Synchronize Mirrors?
> An optimized mirror list points your package manager (`pacman`) to the servers selected by the specified filters and measured rates. Measurements can help select useful mirrors, but they do not guarantee future speed or availability.

## Automated Mirror Selection with `reflector`

The following command uses `reflector` to find mirrors in India synchronized in the last 24 hours, sort them by download speed, and save the result to the official mirror list file.

1.  **Run the `reflector` command:**

    ```bash
    reflector --country India --age 24 --sort rate --save /etc/pacman.d/mirrorlist
    ```

2.  **Understanding the Command:**
    The flags used in this command customize the mirror selection process:

| Flag | Description |
| :--- | :--- |
| `--country India` | Restricts the server search to mirrors located in India. You can change this to your country for better performance. |
| `--age 24` | Selects only mirrors that have been synchronized within the last 24 hours, based on the mirror status data. |
| `--sort rate` | Sorts the filtered mirrors by their download speed, placing the fastest ones at the top of the list. |
| `--save <path>` | Overwrites the specified file with the new list of mirrors. |

## Optional: Verify the Mirror List

After `reflector` finishes, you can inspect the new mirror list to ensure it was generated correctly.

1.  **Open the mirror list file:**

    ```bash
    vim /etc/pacman.d/mirrorlist
    ```

    > [!NOTE]
    > You should see a list of servers, commented with their location (which should be India, based on the command above) and sorted by the measured download rate.
