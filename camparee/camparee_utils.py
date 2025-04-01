import re
import os
import gzip
import itertools
import pandas as pd

class CampareeUtils:
    """
    Utilities for steps in the CAMPAREE expression pipeline.
    """

    # Line format definition for annotation file
    annot_output_format = '{chrom}\t{strand}\t{txStart}\t{txEnd}\t{exonCount}\t{exonStarts}\t{exonEnds}\t{transcriptID}\t{geneID}\t{geneSymbol}\t{biotype}\n'

    @staticmethod
    def create_oneline_seq_fasta(input_fasta_file_path, output_oneline_fasta_file_path):
        """Helper method to convert a FASTA file containing line breaks embedded
        within its sequences to a FASTA file containing each sequence on a single
        line.

        Parameters
        ----------
        input_fasta_file_path : string
            Path to input FASTA file with multi-line sequence data.
        output_oneline_fasta_file_path : string
            Path to output FASTA file to create, where single line sequences
            will be stored.

        Returns
        -------
        set
            Set of unique chromosome/contig names contained in input FASTA file.

        """
        # Use set to track unique chromosomes/contigs since it automatically
        # handles duplicate removal.
        chromosomes = set()

        # Build regex to recognize FASTA header line and store chromosome/contig
        # name (i.e. sequence identifier) in the first group. Chromosome/contig
        # name defined as all non-space characters between ">" and the first
        # whitespace character.
        fasta_header_pattern = re.compile(r'>([^\s]*).*')

        # Flag to denote when a FASTA sequence (and not a header) is being
        # processed.
        building_sequence = False

        # Input FASTA lines saved directly to output file as they are processed
        # to minimize the amount of data stored in memory at a given time.
        with CampareeUtils.open_file(input_fasta_file_path, 'r') as input_fasta_file, \
             CampareeUtils.open_file(output_oneline_fasta_file_path, 'w') as output_fasta_file:

            for line in input_fasta_file:
                if line.startswith(">"):
                    if building_sequence:
                        # Add newline at the end of previous sequence
                        output_fasta_file.write('\n')
                        building_sequence = False

                    sequence_id = re.match(fasta_header_pattern, line).group(1)
                    output_fasta_file.write('>' + sequence_id + '\n')
                    chromosomes.add(sequence_id)

                else:
                    output_fasta_file.write(line.rstrip('\n').upper())
                    building_sequence = True

            # Add line break to end of output oneline fasta file.
            output_fasta_file.write("\n")

        return chromosomes

    @staticmethod
    def create_genome(genome_file_path):
        """
        Creates a genome dictionary from the genome file located at the provided path
        (if compressed, it must have a gz extension).  The filename is assumed to contain the chr sequences without
        line breaks.
        :param genome_file_path: path to reference genome file (either compressed or not)
        :return: genome as a dictionary with the chromosomes/contigs as keys and the sequences as values.
        """
        chr_pattern = re.compile(">([^\s]*).*")
        genome = dict()
        _, file_extension = os.path.splitext(genome_file_path)
        if 'gz' in file_extension:
            with gzip.open(genome_file_path, 'r') as genome_file:
                for chr, seq in itertools.zip_longest(*[genome_file] * 2):
                    chr_match = re.match(chr_pattern, chr.decode("ascii"))
                    if not chr_match:
                        raise CampareeUtilsException(f'Cannot parse the chromosome from the fasta line {chr}.')
                    chr = chr_match.group(1)
                    genome[chr] = seq.decode("ascii").rstrip().upper()
        else:
            with open(genome_file_path, 'r') as reference_genome_file:
                with open(genome_file_path, 'r') as genome_file:
                    for chr, seq in itertools.zip_longest(*[genome_file] * 2):
                        chr_match = re.match(chr_pattern, chr)
                        if not chr_match:
                            raise CampareeUtilsException(f'Cannot parse the chromosome from the fasta line {chr}.')
                        chr = chr_match.group(1)
                        genome[chr] = seq.rstrip().upper()
        return genome

    @staticmethod
    def create_chr_ploidy_data(chr_ploidy_file_path):
        """
        Parses the chr_ploidy_data from its tab delimited resource file into a dictionary of dictionaries like so:
        {
          '1': {'male': 2, 'female': 2},
          'X': {'male': 1, 'female': 2}.
          ...
        }
        :param chr_ploidy_file_path: full path to the chr_ploidy data file
        :return: chr_ploidy_data expressed as a dictionary of dictionary as shown above.
        """
        df = pd.read_csv(chr_ploidy_file_path, sep='\t')
        return df.set_index('chr').to_dict(orient='index')

    @staticmethod
    def compare_genome_sequence_lengths(reference_file_path, genome_1_file_path, genome_2_file_path, chromosomes):
        comparison = {chromosome:[] for chromosome in chromosomes}
        genome = CampareeUtils.create_genome(reference_file_path)
        [comparison[chromosome].append(len(sequence)) for chromosome, sequence
        in genome.items() if chromosome in chromosomes]
        genome = CampareeUtils.create_genome(genome_1_file_path)
        for chromosome in chromosomes:
            seqeunce_length = len(genome.get(chromosome, ''))
            comparison[chromosome].append(seqeunce_length)
        genome = CampareeUtils.create_genome(genome_2_file_path)
        for chromosome in chromosomes:
            seqeunce_length = len(genome.get(chromosome, ''))
            comparison[chromosome].append(seqeunce_length)
        return comparison

    @staticmethod
    def parse_variant_line(line):
        ''' reads a line of a variant file from CAMPAREE'''
        # sample line is: (note tabs and spaces both used)
        # 1:28494 | C:1 | T:1    TOT=2   0.5,0.5 E=1.0
        if line == '':
            return "DONE", 0, {}
        entries = line.split('\t')
        loc_and_vars, total, fractions, entropy = entries
        loc, *variants = loc_and_vars.split(" | ")
        chromosome, position = loc.split(":")
        position = int(position)
        variants = {base: int(count) for base, count in [variant.split(":") for variant in variants]}
        return chromosome, position, variants

    @staticmethod
    def convert_gtf_to_annot_file_format(input_gtf_filename, output_annot_filename):
        """Convert a GTF file to a tab-delimited annotation file with one line
        per transcript. Each line in the annotation file will have the following
        columns:
             1 - chrom
             2 - strand
             3 - txStart
             4 - txEnd
             5 - exonCount
             6 - exonStarts
             7 - exonEnds
             8 - transcript_id
             9 - gene_id
            10 - genesymbol
            11 - biotype
        This method derives transcript info from the "exon" lines in the GTF
        file and assumes the exons are listed in the order they appear in the
        transcript, as opposed to their genomic coordinates. The annotation file
        will list exons in order by plus-strand coordinates, so this method
        reverses the order of exons for all minus-strand transcripts.

        Note, this function will add a header to the output file, marked by a
        '#' character prefix.

        See website for standard 9-column GTF specification:
        https://useast.ensembl.org/info/website/upload/gff.html

        Parameters
        ----------
        input_gtf_filename : string
            Path to GTF file to be converted annotation file format.
        output_annot_filename : string
            Path to output file in annotation format.

        Returns
        -------
        set
            Set of unique chromosome/contig names contained in input GTF file.
            Only GTF entries of "exon" feature type contribute to this set.

        """
        # Use set to track unique chromosomes/contigs since it automatically
        # handles duplicate removal.
        chromosomes = set()

        with CampareeUtils.open_file(input_gtf_filename, 'r') as gtf_file, \
                CampareeUtils.open_file(output_annot_filename, 'w') as output_annot_file:

            #Print annot file header (note the '#' prefix)
            output_annot_file.write("#" + CampareeUtils.annot_output_format.replace('{', '').replace('}', ''))

            #Regex patterns used to extract individual attributes from the 9th
            #column in the GTF file (the "attributes" column)
            txid_pattern = re.compile(r'transcript_id "([^"]+)";')
            geneid_pattern = re.compile(r'gene_id "([^"]+)";')
            genesymbol_pattern = re.compile(r'gene_name "([^"]+)";')
            biotype_pattern = re.compile(r'gene_biotype "([^"]+)";')

            line_data = [] #List of line fields from current line of gtf
            curr_gtf_tx = "" #transcript ID from current line of gtf
            chrom = "" #Chromosome for current transcript
            strand = "" #Strand for current transcript
            txid = "" #ID of current transcript
            geneid = "" #gene ID of current transcript
            genesymbol = "None" #gene symbol of current transcript
            biotype = "None" #Biotype of current transcript
            ex_starts = [] #List of exon start coordinates for current transcript
            ex_stops = [] #List of exon stop coordinates for current transcript
            ex_count = 1 #Number of exons in current transcript

            #Step through GTF until first exon entry (need to prime variables
            #with data from first exon).
            exon_found = False
            for line in gtf_file:
                line_data = line.split("\t")
                #First conditional is to skip any header lines, which would
                #result in a "list index out of range" error when checking the
                #second conditional. Header lines don't contain any tabs, so
                #there is no 3rd list element in line_data, following the split
                #command.
                if len(line_data) > 1 and line_data[2] == "exon":
                    exon_found = True
                    break

            if not exon_found:
                raise CampareeUtilsException(f"ERROR: {input_gtf_filename} contains "
                                             f"no lines with exon feature_type.\n")

            #Prime variables with data from first exon
            chrom = line_data[0]
            strand = line_data[6]
            ex_starts.append(line_data[3])
            ex_stops.append(line_data[4])
            txid = txid_pattern.search(line_data[8]).group(1)
            geneid = geneid_pattern.search(line_data[8]).group(1)
            if genesymbol_pattern.search(line_data[8]):
                genesymbol = genesymbol_pattern.search(line_data[8]).group(1)
            if biotype_pattern.search(line_data[8]):
                biotype = biotype_pattern.search(line_data[8]).group(1)
            chromosomes.add(chrom)

            #process the remainder of the GTF file
            for line in gtf_file:
                line = line.rstrip('\n')
                line_data = line.split("\t")

                if line_data[2] == "exon":
                    curr_gtf_tx = txid_pattern.search(line_data[8]).group(1)

                    #Check transcript in current line is a new transcript
                    if curr_gtf_tx != txid:

                        # Sort exons by numeric chromosomal coordinates. This allows exons
                        # to be listed out of order in the gtf/gff file. Note, exons from
                        # different transcripts still can't be interleaved with on another.
                        ex_starts.sort(key=int)
                        ex_stops.sort(key=int)

                        #Format data from previous transcript and write to annotation file
                        output_annot_file.write(
                            CampareeUtils.annot_output_format.format(
                                chrom=chrom,
                                strand=strand,
                                txStart=ex_starts[0],
                                txEnd=ex_stops[-1],
                                exonCount=ex_count,
                                exonStarts=','.join(ex_starts),
                                exonEnds=','.join(ex_stops),
                                transcriptID=txid,
                                geneID=geneid,
                                geneSymbol=genesymbol,
                                biotype=biotype
                            )
                        )

                        #Load data from new transcript into appropriate variables
                        txid = curr_gtf_tx
                        chrom = line_data[0]
                        strand = line_data[6]
                        ex_count = 1
                        ex_starts = [line_data[3]]
                        ex_stops = [line_data[4]]
                        geneid = geneid_pattern.search(line_data[8]).group(1)
                        if genesymbol_pattern.search(line_data[8]):
                            genesymbol = genesymbol_pattern.search(line_data[8]).group(1)
                        else:
                            genesymbol = "None"
                        if biotype_pattern.search(line_data[8]):
                            biotype = biotype_pattern.search(line_data[8]).group(1)
                        else:
                            biotype = "None"
                        chromosomes.add(chrom)

                    #This exon is strill from the same transcript.
                    else:
                        ex_starts.append(line_data[3])
                        ex_stops.append(line_data[4])
                        ex_count += 1

            #Finish processing last transcript in GTF file

            # Sort exons by numeric chromosomal coordinates. This allows exons
            # to be listed out of order in the gtf/gff file. Note, exons from
            # different transcripts still can't be interleaved with on another.
            ex_starts.sort(key=int)
            ex_stops.sort(key=int)

            #Format data from last transcript and write to annotation file
            output_annot_file.write(
                CampareeUtils.annot_output_format.format(
                    chrom=chrom,
                    strand=strand,
                    txStart=ex_starts[0],
                    txEnd=ex_stops[-1],
                    exonCount=ex_count,
                    exonStarts=','.join(ex_starts),
                    exonEnds=','.join(ex_stops),
                    transcriptID=txid,
                    geneID=geneid,
                    geneSymbol=genesymbol,
                    biotype=biotype
                )
            )

        return chromosomes

    @staticmethod
    def open_file(filename, mode='r'):
        """Helper method which can open gzipped files by checking the filename
        for the '.gz' extension. If no '.gz' extension found, this method uses
        the standard open() function.

        Parameters
        ----------
        filename : string
            Name of the file to open. If this filename has a '.gz' extension,
            the function uses the gzip package. If not, it uses open() function.
        mode : string
            Access mode (e.g. 'r' - read, 'w' - write) passed to the open function.
            Note, to open a file in binary mode, need to explicitly end the mode
            code with a 'b'. [DEFAULT: 'r' - open file for reading in text mode].

        Returns
        -------
        file object
            Pointer to the opened file.

        """
        file_pointer = None

        # gzip.open defaults to opening files in binary modes and requires
        # explicit inclusion of 't' in the mode to open it as text. However,
        # the built-in open() function defaults to text mode. To normalize the
        # behavior between these two methods to match the built-in open(), the
        # code here checks for the presence of a 'b' (for binary mode). If
        # there's no 'b' or 't', the code appends a 't', so the mode will
        # always default to text mode.
        updated_mode = mode
        if 'b' not in updated_mode.lower() and 't' not in updated_mode.lower():
            updated_mode = updated_mode + 't'

        if filename.endswith('.gz'):
            file_pointer = gzip.open(filename=filename, mode=updated_mode)
        else:
            file_pointer = open(file=filename, mode=updated_mode)

        return file_pointer

class CampareeException(Exception):
    """Base class for other Camparee exceptions."""
    pass

class CampareeUtilsException(CampareeException):
    """Base class for Camparee Utils exceptions."""
    pass


if __name__ == "__main__":
    #CampareeUtils.create_genome("../../resources/index_files/hg38/Homo_sapiens.GRCh38.reference_genome.fa")
    chr_ploidy_data_ = CampareeUtils.create_chr_ploidy_data('../../resources/index_files/GRCh38/Homo_sapiens.GRCh38.chr_ploidy.txt')
    reference_genome_file_path = '../../resources/index_files/GRCh38/Homo_sapiens.GRCh38.reference_genome.fa.gz'
    data_directory_path = '../../data/pipeline_results_run89/expression_pipeline/data'
    sample_data_folder = os.path.join(data_directory_path, f'sample1')
    results = CampareeUtils.compare_genome_sequence_lengths(reference_genome_file_path,
                                                              os.path.join(sample_data_folder, 'custom_genome_1.fa'),
                                                              os.path.join(sample_data_folder, 'custom_genome_2.fa'),
                                                              chr_ploidy_data_.keys())
    df = pd.DataFrame.from_dict(results, orient='index', columns=['Reference Genome', 'Genome 1', 'Genome 2'])
    print(df)
