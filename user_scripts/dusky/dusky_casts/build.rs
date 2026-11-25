fn main() -> std::io::Result<()> {
    println!("cargo:rerun-if-changed=proto/api.proto");
    println!("cargo:rerun-if-changed=proto/sync.proto");
    println!("cargo:rerun-if-changed=proto/bootstrap.proto");
    prost_build::compile_protos(
        &[
            "proto/api.proto",
            "proto/sync.proto",
            "proto/bootstrap.proto",
        ],
        &["proto"],
    )
}
